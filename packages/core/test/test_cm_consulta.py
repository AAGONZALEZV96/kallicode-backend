"""Contrato cm-consulta-v1: resolución de {{nuggets}} (KC-CM-08-3), sin base de datos.

Prueba de contrato: el JSON Schema congelado en contratos/cm-consulta-v1.schema.json es exactamente
el que generan los modelos, y toda respuesta valida contra él. Si un cambio en los modelos rompe
este test, el contrato con Backend cambió: regenera con `python -m kallicode_core.cm_consulta`,
registra el cambio en contratos/cambios-anunciados.yaml y pide la aprobación de Backend.
"""

from __future__ import annotations

import json
import random
from typing import Any

import jsonschema
import pytest
from kallicode_core import cm_consulta as cc
from kallicode_core.cm_consulta import (
    ConsultaNuggetsIn,
    ConsultaNuggetsOut,
    VariablePlantilla,
    construir_respuesta,
    parametros_sql,
    resolver_nuggets,
    seleccionar_para_presupuesto,
)
from pydantic import ValidationError


def _fila(
    i: int, prioridad: int = 1, score: float = 0.5, regla: str | None = None
) -> dict[str, Any]:
    return {
        "id": f"ng_{i:03d}",
        "regla": regla or f"Regla número {i}.",
        "tipo": "regla_negocio" if i % 2 else "restriccion_tecnica",
        "seccion": f"Sección {i}",
        "evidencia_ref": f"chunk_{i}",
        "prioridad": prioridad,
        "score": score,
    }


def _consulta(**kw: Any) -> ConsultaNuggetsIn:
    return ConsultaNuggetsIn(**{"variable": "nuggets", **kw})


# ---------------------------------------------------------------------------
# Prueba de contrato
# ---------------------------------------------------------------------------
def test_contrato_congelado_coincide_con_los_modelos() -> None:
    congelado = json.loads(cc.RUTA_CONTRATO.read_text(encoding="utf-8"))
    assert congelado == cc.generar_contrato(), (
        "El contrato cm-consulta-v1 cambió: regenera contratos/cm-consulta-v1.schema.json con "
        "`uv run python -m kallicode_core.cm_consulta` y anúncialo a Backend."
    )
    assert cc.RUTA_CONTRATO.read_text(encoding="utf-8") == cc.serializar_contrato()


def test_contrato_es_estricto_y_declara_presupuesto() -> None:
    contrato = cc.generar_contrato()
    jsonschema.Draft202012Validator.check_schema(contrato["entrada"])
    jsonschema.Draft202012Validator.check_schema(contrato["salida"])
    assert contrato["entrada"]["additionalProperties"] is False
    assert contrato["salida"]["additionalProperties"] is False
    assert contrato["x-presupuesto"]["regla_max_caracteres"] == 280
    assert contrato["x-presupuesto"]["presupuesto_por_defecto"] == 20 * 283


@pytest.mark.parametrize("n_filas", [0, 3, 40])
def test_respuestas_validan_contra_el_contrato_congelado(n_filas: int) -> None:
    salida = json.loads(cc.RUTA_CONTRATO.read_text(encoding="utf-8"))["salida"]
    r = construir_respuesta(_consulta(), [_fila(i) for i in range(n_filas)])
    jsonschema.validate(r.model_dump(mode="json"), salida)


def test_entrada_valida_contra_el_contrato_y_extra_se_rechaza() -> None:
    entrada = json.loads(cc.RUTA_CONTRATO.read_text(encoding="utf-8"))["entrada"]
    jsonschema.validate(_consulta(ficheros=["a.py"]).model_dump(mode="json"), entrada)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"variable": "nuggets", "tenant_id": "org_x"}, entrada)
    with pytest.raises(ValidationError):
        ConsultaNuggetsIn.model_validate({"variable": "nuggets", "tenant_id": "org_x"})


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "datos",
    [
        {"variable": "module_conventions"},
        {"variable": "nuggets_for_file"},
        {"variable": "nuggets_for_file", "ficheros": ["a.py", "b.py"]},
        {"variable": "nuggets", "presupuesto_caracteres": -1},
        {"variable": "nuggets", "presupuesto_caracteres": cc.PRESUPUESTO_MAXIMO + 1},
        {"variable": "otra"},
    ],
)
def test_entrada_incoherente_se_rechaza(datos: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ConsultaNuggetsIn.model_validate(datos)


def test_parametros_por_variable() -> None:
    p = parametros_sql(_consulta(nodos=["sym_1"], ficheros=["src/a.py"]))
    assert p == {
        "nodos": ["sym_1"],
        "ficheros": ["src/a.py"],
        "prefijo": None,
        "tipos": None,
        "incluir_generales": True,
        "limite": cc.MAX_CANDIDATOS,
    }
    modulo = parametros_sql(
        ConsultaNuggetsIn(variable="module_conventions", modulo="src/bill_ing%/", nodos=["x"])
    )
    assert modulo["prefijo"] == "src/bill!_ing!%/%"  # comodines del nombre escapados
    assert modulo["nodos"] == [] and modulo["ficheros"] == []
    assert modulo["tipos"] == ["restriccion_tecnica"] and modulo["incluir_generales"] is False
    fichero = parametros_sql(ConsultaNuggetsIn(variable="nuggets_for_file", ficheros=["a.py"]))
    assert fichero["ficheros"] == ["a.py"] and fichero["incluir_generales"] is False
    assert fichero["tipos"] is None and fichero["prefijo"] is None


# ---------------------------------------------------------------------------
# Recorte: reglas enteras, en orden, sin que una menor desplace a una mayor
# ---------------------------------------------------------------------------
def test_recorte_nunca_parte_una_regla_y_se_detiene_en_la_primera_que_no_cabe() -> None:
    reglas = ["a" * 10, "b" * 30, "c" * 5]  # líneas de 12, 32 y 7 caracteres
    assert seleccionar_para_presupuesto(reglas, 12) == [0]
    assert seleccionar_para_presupuesto(reglas, 44) == [0]  # 12 + 1 + 32 = 45 > 44
    assert seleccionar_para_presupuesto(reglas, 45) == [0, 1]
    assert seleccionar_para_presupuesto(reglas, 53) == [0, 1, 2]  # 45 + 1 + 7
    assert seleccionar_para_presupuesto(reglas, 11) == []
    assert seleccionar_para_presupuesto(reglas, 0) == []


def test_recorte_salta_vacias_y_largas_y_respeta_el_maximo() -> None:
    assert seleccionar_para_presupuesto(["", "x" * 281, "ok"], 100) == [2]
    assert seleccionar_para_presupuesto(["r"] * 30, 10_000) == list(range(20))
    assert seleccionar_para_presupuesto(["r"] * 30, 10_000, max_reglas=3) == [0, 1, 2]


@pytest.mark.parametrize("semilla", range(25))
def test_propiedad_el_texto_cabe_y_contiene_reglas_enteras(semilla: int) -> None:
    rnd = random.Random(semilla)
    filas = [
        _fila(i, regla="x" * rnd.randint(1, cc.REGLA_MAX_CHARS)) for i in range(rnd.randint(0, 40))
    ]
    presupuesto = rnd.randint(0, 3000)
    r = construir_respuesta(_consulta(presupuesto_caracteres=presupuesto), filas)

    assert r.caracteres_usados == len(r.texto) <= presupuesto
    lineas = r.texto.split("\n") if r.texto else []
    assert lineas == [f"- {n.regla}" for n in r.nuggets]  # cada línea es una regla completa
    assert [n.id for n in r.nuggets] == [f["id"] for f in filas[: len(r.nuggets)]]  # prefijo
    assert r.omitidos == len(filas) - len(r.nuggets)
    if r.omitidos and len(r.nuggets) < cc.MAX_NUGGETS_EN_CONTEXTO:
        siguiente = len(f"- {filas[len(r.nuggets)]['regla']}") + (1 if r.nuggets else 0)
        assert r.caracteres_usados + siguiente > presupuesto  # se paró porque no cabía


def test_respuesta_conserva_el_orden_de_la_consulta_y_mapea_relevancia() -> None:
    filas = [_fila(3, 1, 0.9), _fila(1, 2, 0.0), _fila(2, 3, 0.0)]
    r = construir_respuesta(_consulta(), filas)
    assert [(n.id, n.relevancia) for n in r.nuggets] == [
        ("ng_003", "directa"),
        ("ng_001", "alcance"),
        ("ng_002", "general"),
    ]
    assert r.version == "cm-consulta-v1" and r.variable is VariablePlantilla.NUGGETS
    assert r.texto == "- Regla número 3.\n- Regla número 1.\n- Regla número 2."


def test_sin_reglas_la_respuesta_es_vacia_y_valida() -> None:
    r = construir_respuesta(_consulta(), [])
    assert r == ConsultaNuggetsOut(
        variable=VariablePlantilla.NUGGETS,
        texto="",
        nuggets=[],
        omitidos=0,
        presupuesto_caracteres=cc.PRESUPUESTO_DEFECTO,
        caracteres_usados=0,
    )


def test_score_se_acota_y_espacios_se_normalizan() -> None:
    r = construir_respuesta(
        _consulta(), [_fila(1, score=1.0000001, regla="  Regla \n con  saltos ")]
    )
    assert r.nuggets[0].score == 1.0
    assert r.texto == "- Regla con saltos"


# ---------------------------------------------------------------------------
# Resolutor async (Backend)
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
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, sql: Any, params: dict[str, Any]) -> _Resultado:
        self.llamadas.append((str(sql), params))
        return _Resultado(self.filas)


async def test_resolver_async_usa_la_consulta_del_contrato() -> None:
    sesion = _Sesion([_fila(1), _fila(2)])
    consulta = _consulta(ficheros=["src/a.py"])

    r = await resolver_nuggets(sesion, consulta)  # type: ignore[arg-type]

    sql, params = sesion.llamadas[0]
    assert sql == cc.SQL_NUGGETS_APLICABLES
    assert params == parametros_sql(consulta)
    assert [n.id for n in r.nuggets] == ["ng_001", "ng_002"]


def test_la_consulta_filtra_aprobados_del_tenant_activo_y_ordena_de_forma_total() -> None:
    sql = " ".join(cc.SQL_NUGGETS_APLICABLES.split())
    assert "n.estado = 'aprobado'" in sql
    assert sql.count("tenant_id = core.current_tenant()") == 10  # cada tabla, además de RLS
    assert 'ORDER BY prioridad, score DESC, resuelto_en, id COLLATE "C"' in sql
