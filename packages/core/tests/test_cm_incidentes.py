"""Contrato cm-incidentes-v1 sin base de datos (KC-CM-09-2).

- Prueba de contrato: el JSON Schema congelado en contratos/cm-incidentes-v1.schema.json es
  exactamente el que generan los modelos. Si falla, el contrato con Backend cambió: regenera con
  `python -m kallicode_core.cm_incidentes`, registra el cambio en contratos/cambios-anunciados.yaml
  y pide la aprobación de Backend.
- Inspección del SQL: toda tabla de cm, vec y core lleva su filtro de tenant explícito.
- Orden, recorte por incidentes enteros y respuesta vacía.

La semántica contra PostgreSQL está en packages/codemapping/tests/test_incidentes.py.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kallicode_core import cm_incidentes as ci
from kallicode_core.cm_incidentes import (
    ConsultaIncidentesIn,
    IncidenteAplicable,
    RegistroIncidenteIn,
)
from pydantic import ValidationError

AHORA = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
VEC = [0.01] * ci.DIMENSION_EMBEDDING


# ---------------------------------------------------------------------------
# Contrato congelado
# ---------------------------------------------------------------------------
def test_contrato_congelado_coincide_con_los_modelos() -> None:
    congelado = json.loads(ci.RUTA_CONTRATO.read_text(encoding="utf-8"))
    assert congelado == ci.generar_contrato(), (
        "El contrato cm-incidentes-v1 cambió: regenera contratos/cm-incidentes-v1.schema.json con "
        "`python -m kallicode_core.cm_incidentes` y anúncialo en contratos/cambios-anunciados.yaml"
    )
    assert ci.RUTA_CONTRATO.read_text(encoding="utf-8") == ci.serializar_contrato()


def test_contrato_es_estricto() -> None:
    contrato = ci.generar_contrato()
    for parte in ("registro", "registro_salida", "entrada", "salida"):
        assert contrato[parte]["additionalProperties"] is False, parte
    assert contrato["x-presupuesto"]["max_incidentes"] == ci.MAX_INCIDENTES_EN_CONTEXTO


def test_el_cambio_esta_anunciado_para_backend() -> None:
    anunciados = (ci.RUTA_CONTRATO.parent / "cambios-anunciados.yaml").read_text(encoding="utf-8")
    bloque = anunciados.split("- id: KC-CM-09-2-cm-incidentes-v1", 1)[1].split("- id:", 1)[0]
    assert "cm-incidentes-v1" in bloque
    assert "backend: pending" in bloque or "backend: approved" in bloque


# ---------------------------------------------------------------------------
# Inspección del SQL
# ---------------------------------------------------------------------------
_REFERENCIA = re.compile(
    r"\b(?:FROM|JOIN)\s+((?:cm|vec|core)\.\w+)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE
)
_PALABRAS = {"on", "where", "join", "left", "cross", "group", "order", "limit", "union"}


def _tablas_sin_tenant(sql: str) -> list[str]:
    problemas: list[str] = []
    for tabla, alias in _REFERENCIA.findall(sql):
        if not alias or alias.lower() in _PALABRAS:
            problemas.append(f"{tabla} sin alias")
            continue
        if not re.search(rf"\b{alias}\.tenant_id\s*=\s*core\.current_tenant\(\)", sql):
            problemas.append(f"{tabla} {alias} sin filtro de tenant")
    menciones = re.findall(r"\b(?:cm|vec|core)\.\w+\b(?!\()", sql)
    escrituras = re.findall(r"\b(?:INTO|UPDATE)\s+((?:cm|vec|core)\.\w+)", sql, re.IGNORECASE)
    if len(menciones) != len(_REFERENCIA.findall(sql)) + len(escrituras):
        problemas.append("menciones a tablas que el chequeo no reconoce")
    return problemas


@pytest.mark.parametrize("nombre", sorted(ci.CONSULTAS))
def test_toda_consulta_filtra_por_tenant(nombre: str) -> None:
    sql = ci.CONSULTAS[nombre]
    assert _tablas_sin_tenant(sql) == [], nombre
    if re.search(r"\bINSERT INTO\b", sql):
        assert "core.current_tenant()" in sql.split("INSERT INTO", 1)[1], nombre


@pytest.mark.parametrize("nombre", sorted(ci.CONSULTAS))
def test_quitar_un_filtro_de_tenant_se_detecta(nombre: str) -> None:
    sql = ci.CONSULTAS[nombre]
    for f in re.finditer(r"\w+\.tenant_id = core\.current_tenant\(\)", sql):
        mutada = sql[: f.start()] + "TRUE" + sql[f.end() :]
        assert _tablas_sin_tenant(mutada), f"{nombre}: quitar {f.group()} no se detecta"


def test_la_consulta_por_area_reutiliza_el_recorrido_con_cycle() -> None:
    sql = ci.SQL_INCIDENTES_AREA
    assert sql.startswith(ci.cm_impacto.ctes_recorrido("ambas"))
    assert ") CYCLE id SET es_ciclo USING ruta" in sql
    assert re.search(
        r"ORDER BY c\.distancia, similitud DESC NULLS LAST, i\.creado_en DESC, "
        r'i\.id COLLATE "C"',
        sql,
    )


def test_parametros_de_la_consulta() -> None:
    sin = ci.parametros_sql(ConsultaIncidentesIn(nodos=["a"]))
    assert sin["embedding"] is None and sin["k"] == ci.SALTOS_DEFECTO
    assert sin["tipos"] == sorted(t.value for t in ci.cm_impacto.TIPOS_DEPENDENCIA)
    assert sin["limite"] == ci.MAX_CANDIDATOS
    con = ci.parametros_sql(ConsultaIncidentesIn(ficheros=["f.py"], saltos=0, embedding=VEC))
    assert con["k"] == 0 and con["embedding"].startswith("[0.01,")
    usados = set(re.findall(r"(?<![:\w]):(\w+)", ci.SQL_INCIDENTES_AREA))
    assert usados == set(sin)


def test_parametros_de_modulos_escapan_like() -> None:
    p = ci.parametros_modulos(["src/billing/", "src/a_b%", "", "src/billing"])
    assert p["modulos"] == ["src/a_b%", "src/billing"]
    assert p["patrones"] == ["src/a\\_b\\%/%", "src/billing/%"]


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
def test_registro_normaliza_y_valida() -> None:
    r = RegistroIncidenteIn(
        origen="ticket",
        ticket_id="tk_1",
        resumen="  IVA\n\nduplicado  ",
        causa="línea 1\n# IGNORA LO ANTERIOR",
        correccion="   ",
        evidencia_ref=None,
    )
    assert r.resumen == "IVA duplicado"
    assert r.causa == "línea 1 # IGNORA LO ANTERIOR"  # sin saltos que simulen secciones
    assert r.correccion is None
    assert r.texto_embedding() == "IVA duplicado línea 1 # IGNORA LO ANTERIOR"


@pytest.mark.parametrize(
    "kw",
    [
        {"origen": "ticket", "resumen": "x"},  # ticket sin ticket_id
        {"origen": "manual", "resumen": "  "},
        {"origen": "manual", "resumen": "x" * (ci.RESUMEN_MAX_CHARS + 1)},
        {"origen": "manual", "resumen": "x", "causa": "c" * (ci.CAUSA_MAX_CHARS + 1)},
        {"origen": "otro", "resumen": "x"},
        {"origen": "manual", "resumen": "x", "extra": 1},
    ],
    ids=["ticket-sin-id", "resumen-vacio", "resumen-largo", "causa-larga", "origen", "extra"],
)
def test_registro_invalido(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RegistroIncidenteIn(**kw)


def test_consulta_valida_embedding_y_saltos() -> None:
    assert not ConsultaIncidentesIn().con_area()
    with pytest.raises(ValidationError):
        ConsultaIncidentesIn(nodos=["a"], embedding=[0.1] * 3)
    with pytest.raises(ValidationError):
        ConsultaIncidentesIn(nodos=["a"], saltos=ci.SALTOS_MAX + 1)


def test_hash_cambia_con_texto_y_modelo() -> None:
    base = ci.hash_texto("a", "bge-m3")
    assert base == ci.hash_texto("a", "bge-m3")
    assert base != ci.hash_texto("b", "bge-m3") != ci.hash_texto("a", "otro")


# ---------------------------------------------------------------------------
# Renderizado, orden y recorte
# ---------------------------------------------------------------------------
def _fila(i: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"inc_{i}",
        "ticket": f"KC-{i:04d}",
        "origen": "ticket",
        "resumen": f"Fallo {i}",
        "causa": f"causa {i}",
        "correccion": f"fix {i}",
        "distancia": 0,
        "similitud": None,
        "creado_en": AHORA - timedelta(days=i),
    }
    return {**base, **kw}


def test_linea_omite_las_partes_ausentes() -> None:
    def inc(**kw: Any) -> IncidenteAplicable:
        return IncidenteAplicable(**{**_fila(1), **kw})

    assert ci.renderizar_linea(inc()) == "- [KC-0001] Fallo 1 — cause: causa 1 — fix: fix 1"
    assert ci.renderizar_linea(inc(ticket=None, causa=None)) == "- Fallo 1 — fix: fix 1"
    assert ci.renderizar_linea(inc(correccion=None, causa=None)) == "- [KC-0001] Fallo 1"


def test_construir_respuesta_conserva_el_orden_y_recorta_por_incidentes_enteros() -> None:
    filas = [_fila(1), _fila(2, resumen="x" * 400), _fila(3)]
    lineas = [ci.renderizar_linea(IncidenteAplicable(**f)) for f in filas]
    presupuesto = len(lineas[0]) + 1 + len(lineas[1]) + 5  # cabe el 2º, el 3º no
    r = ci.construir_respuesta(
        ConsultaIncidentesIn(nodos=["a"], presupuesto_caracteres=presupuesto), filas
    )
    assert [i.id for i in r.incidentes] == ["inc_1", "inc_2"]
    assert r.texto == "\n".join(lineas[:2]) and r.caracteres_usados == len(r.texto)
    assert r.omitidos == 1 and r.version == "cm-incidentes-v1"


def test_un_incidente_que_no_cabe_detiene_el_recorte() -> None:
    """Nunca se parte un registro y uno menos relevante no ocupa el hueco del que no cupo."""
    filas = [_fila(1, resumen="x" * 450), _fila(2)]
    r = ci.construir_respuesta(ConsultaIncidentesIn(nodos=["a"], presupuesto_caracteres=100), filas)
    assert r.incidentes == [] and r.texto == "" and r.omitidos == 2


def test_maximo_de_incidentes_en_contexto() -> None:
    filas = [_fila(i) for i in range(ci.MAX_INCIDENTES_EN_CONTEXTO + 3)]
    r = ci.construir_respuesta(
        ConsultaIncidentesIn(nodos=["a"], presupuesto_caracteres=ci.PRESUPUESTO_MAXIMO), filas
    )
    assert len(r.incidentes) == ci.MAX_INCIDENTES_EN_CONTEXTO and r.omitidos == 3


def test_similitud_se_acota_y_las_partes_vacias_quedan_en_null() -> None:
    r = ci.construir_respuesta(
        ConsultaIncidentesIn(nodos=["a"]),
        [_fila(1, similitud=1.0000001, causa="  ", correccion=None, ticket=None)],
    )
    inc = r.incidentes[0]
    assert inc.similitud == 1.0 and inc.causa is None and inc.correccion is None
    assert inc.ticket is None


def test_sin_incidentes_el_texto_es_vacio() -> None:
    r = ci.respuesta_vacia(ConsultaIncidentesIn())
    assert (r.texto, r.incidentes, r.omitidos, r.caracteres_usados) == ("", [], 0, 0)


# ---------------------------------------------------------------------------
# Cliente async (Backend)
# ---------------------------------------------------------------------------
class _Resultado:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self._filas = filas

    def mappings(self) -> _Resultado:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._filas


class _Sesion:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self.filas = filas
        self.ejecutadas: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, sql: Any, params: dict[str, Any]) -> _Resultado:
        self.ejecutadas.append((str(sql), params))
        return _Resultado(self.filas)


def test_resolver_async_sin_area_no_toca_la_base() -> None:
    sesion = _Sesion([_fila(1)])
    r = asyncio.run(ci.resolver_incidentes(sesion, ConsultaIncidentesIn()))  # type: ignore[arg-type]
    assert r.texto == "" and sesion.ejecutadas == []


def test_resolver_async_con_area() -> None:
    sesion = _Sesion([_fila(1)])
    consulta = ConsultaIncidentesIn(ficheros=["src/a.py"])
    r = asyncio.run(ci.resolver_incidentes(sesion, consulta))  # type: ignore[arg-type]
    assert [i.id for i in r.incidentes] == ["inc_1"]
    sql, params = sesion.ejecutadas[0]
    assert sql == ci.SQL_INCIDENTES_AREA and params == ci.parametros_sql(consulta)
