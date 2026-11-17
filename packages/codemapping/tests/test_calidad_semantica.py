"""Calidad semántica del grafo sobre 50 preguntas con respuesta conocida (KC-CM-12-4).

Conjunto: tests/evaluacion/preguntas.yaml sobre el repositorio tests/evaluacion/repo_eval. La
respuesta de cada pregunta está escrita a mano a partir del código; el grafo se construye por la ruta
real del pipeline y se interroga como kallicode_app (RLS), igual que en producción.

Sin base de datos corren las pruebas del conjunto y de las métricas. Con PostgreSQL (make up && make
db):
- las 40 preguntas de impacto superan los umbrales del ticket (precisión > 0,85 y exhaustividad > 0,80)
  con cualquier embebedor, porque no dependen de él;
- ninguna pregunta empeora respecto a la línea base versionada (docs/calidad/linea_base_calidad_cm.json);
- las dos rutas de cm.impacto() (cm.reachability y CTE recursivo) devuelven lo mismo;
- con KC_EVAL_TEI_URL (BGE-M3, el modelo de producción), las 50 preguntas superan los umbrales. Es el
  criterio del ticket y lo mide el job nocturno .github/workflows/calidad_semantica_nocturno.yml.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import evaluacion_calidad as ev
from kallicode_codemapping.graph_loader import normalizar_dsn
from kallicode_codemapping.parser_treesitter import parse_file

CONJUNTO = ev.cargar_conjunto()
FUENTES = ev.fuentes_repo()
TABLAS, FKS = ev.cargar_esquema()
SIMBOLOS = [s for f in FUENTES for s in parse_file(f)]
INVENTARIO = ev.inventario(FUENTES, SIMBOLOS, TABLAS)


# ---------------------------------------------------------------------------
# El conjunto de evaluación
# ---------------------------------------------------------------------------
def test_el_conjunto_tiene_50_preguntas_validas_sobre_repo_eval() -> None:
    assert ev.errores_conjunto(CONJUNTO, INVENTARIO) == []
    assert len(CONJUNTO.preguntas) == ev.N_PREGUNTAS
    assert len(CONJUNTO.de_operacion(*ev.OPERACIONES_GRAFO)) == ev.N_PREGUNTAS_GRAFO
    assert len(CONJUNTO.de_operacion("busqueda")) == ev.N_PREGUNTAS - ev.N_PREGUNTAS_GRAFO
    assert {p.operacion for p in CONJUNTO.preguntas} == set(ev.OPERACIONES)
    assert (CONJUNTO.umbral_precision, CONJUNTO.umbral_exhaustividad) == (0.85, 0.80)


def test_cada_descripcion_corresponde_a_un_simbolo_con_nombre() -> None:
    descripciones = ev.cargar_descripciones()
    con_nombre = {s for s in INVENTARIO.simbolos if not ev.es_anonimo(s)}
    assert set(descripciones) == con_nombre


def test_el_esquema_tiene_las_fk_entre_tablas_declaradas() -> None:
    tablas = {(t["esquema"], t["name"]) for t in TABLAS}
    for fk in FKS:
        assert (fk["from_schema"], fk["from_table"]) in tablas
        assert (fk["to_schema"], fk["to_table"]) in tablas


def test_una_clave_inexistente_o_la_semilla_en_esperado_invalidan_el_conjunto(
    tmp_path: Path,
) -> None:
    ruta = tmp_path / "preguntas.yaml"
    ruta.write_text(
        """
version: prueba
umbrales: {precision: 0.85, exhaustividad: 0.80}
preguntas:
  - id: X-1
    operacion: impacto
    semilla: {nodo: facturacion/utilidades.py::redondear}
    k: 1
    esperado: [facturacion/utilidades.py::redondear, no/existe.py::nada]
    justificacion: prueba
""",
        encoding="utf-8",
    )
    errores = ev.errores_conjunto(ev.cargar_conjunto(ruta), INVENTARIO)
    assert any("no puede estar en esperado" in e for e in errores)
    assert any("no/existe.py::nada no existe" in e for e in errores)
    assert any("tiene 1 preguntas, no 50" in e for e in errores)
    assert any("40 preguntas de impacto" in e for e in errores)


@pytest.mark.parametrize(
    ("pregunta", "mensaje"),
    [
        ({"operacion": "otra", "esperado": [], "justificacion": "j"}, "operación desconocida"),
        ({"operacion": "impacto", "k": 1, "justificacion": "j"}, "la semilla es una de"),
        (
            {"operacion": "impacto", "semilla": {"nodo": "a"}, "k": 9, "justificacion": "j"},
            "k fuera de",
        ),
        ({"operacion": "busqueda", "texto": "", "justificacion": "j"}, "texto y al menos"),
        ({"operacion": "busqueda", "texto": "t", "esperado": ["a"]}, "falta la justificación"),
        (
            {"operacion": "busqueda", "texto": "t", "esperado": ["a", "a"], "justificacion": "j"},
            "repetidos",
        ),
    ],
)
def test_preguntas_mal_formadas_se_rechazan(
    tmp_path: Path, pregunta: dict[str, Any], mensaje: str
) -> None:
    ruta = tmp_path / "preguntas.yaml"
    datos = {
        "version": "v",
        "umbrales": {"precision": 0.85, "exhaustividad": 0.8},
        "preguntas": [{"id": "X-1", **pregunta}],
    }
    ruta.write_text(json.dumps(datos), encoding="utf-8")  # JSON es YAML válido
    with pytest.raises(ValueError, match=mensaje):
        ev.cargar_conjunto(ruta)


def test_ids_repetidos_se_rechazan(tmp_path: Path) -> None:
    p = {"id": "X", "operacion": "busqueda", "texto": "t", "esperado": ["a"], "justificacion": "j"}
    datos = {"version": "v", "umbrales": {"precision": 1, "exhaustividad": 1}, "preguntas": [p, p]}
    ruta = tmp_path / "p.yaml"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    with pytest.raises(ValueError, match="repetidos"):
        ev.cargar_conjunto(ruta)


def test_la_busqueda_usa_r_precision() -> None:
    for p in CONJUNTO.de_operacion("busqueda"):
        assert p.k == len(p.esperado)


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("devueltos", "esperado", "p", "r"),
    [
        (set(), set(), 1.0, 1.0),
        (set(), {"a"}, 0.0, 0.0),
        ({"a"}, set(), 0.0, 1.0),
        ({"a", "b"}, {"a"}, 0.5, 1.0),
        ({"a"}, {"a", "b", "c", "d"}, 1.0, 0.25),
    ],
)
def test_precision_y_exhaustividad_con_los_casos_vacios(
    devueltos: set[str], esperado: set[str], p: float, r: float
) -> None:
    assert ev.precision_exhaustividad(devueltos, esperado) == (p, r)


def _pregunta(id_: str, esperado: set[str], operacion: ev.Operacion = "impacto") -> ev.Pregunta:
    return ev.Pregunta(id=id_, operacion=operacion, esperado=frozenset(esperado), justificacion="j")


def test_los_anonimos_devueltos_se_ignoran() -> None:
    r = ev.resultado(_pregunta("A", {"x.ts::f"}), ["x.ts::f", "x.ts::<anonymous>"])
    assert (r.precision, r.exhaustividad, r.acierta) == (1.0, 1.0, True)


def test_resumen_macro_micro_y_por_operacion() -> None:
    resultados = [
        ev.resultado(_pregunta("A", {"a", "b"}), ["a", "b"]),  # 1 / 1
        ev.resultado(_pregunta("B", {"a", "b", "c", "d"}), ["a", "z"]),  # 0,5 / 0,25
        ev.resultado(_pregunta("C", {"q"}, "busqueda"), ["q"]),  # 1 / 1
    ]
    conjunto = ev.Conjunto("v", 0.85, 0.80, ())
    resumen = ev.resumir(resultados, conjunto)
    assert resumen["precision"] == round(2.5 / 3, 4)
    assert resumen["exhaustividad"] == round(2.25 / 3, 4)
    assert resumen["precision_micro"] == round(4 / 5, 4)
    assert resumen["exhaustividad_micro"] == round(4 / 7, 4)
    assert resumen["por_operacion"]["impacto"]["preguntas"] == 2
    assert resumen["fallidas"] == ["B"]
    assert resumen["cumple"] is False
    assert resultados[1].falsos_positivos == ["z"]
    assert resultados[1].falsos_negativos == ["b", "c", "d"]
    assert resultados[1].como_dict()["precision"] == 0.5


def test_la_serie_temporal_acumula_un_punto_por_ejecucion(tmp_path: Path) -> None:
    resultados = [ev.resultado(_pregunta("A", {"a"}), ["a"])]
    conjunto = ev.Conjunto("calidad-cm-v1", 0.85, 0.80, ())
    resumen = ev.resumir(resultados, conjunto) | {"rutas_discrepantes": []}
    evaluacion = ev.Evaluacion(resultados, resumen)
    ruta = tmp_path / "serie" / "serie.jsonl"
    momento = datetime(2026, 11, 17, 5, 45, tzinfo=UTC)
    for _ in range(3):
        serie = ev.anadir_a_serie(ruta, ev.punto_serie(evaluacion, conjunto, "ngramas", momento))
    assert len(serie) == 3 == len(ev.leer_serie(ruta))
    assert serie[-1]["fecha"] == "2026-11-17T05:45:00+00:00"
    assert serie[-1]["cumple"] is True
    assert serie[-1]["por_operacion"]["impacto"] == {"precision": 1.0, "exhaustividad": 1.0}

    texto = ev.resumen_markdown(evaluacion, serie, ultimos=2)
    assert "**cumple**" in texto
    assert texto.count("2026-11-17T05:45:00+00:00") == 2


def test_el_resumen_markdown_lista_los_fallos_y_las_rutas_discrepantes() -> None:
    resultados = [ev.resultado(_pregunta("B", {"a", "b"}), ["a", "z"])]
    resumen = ev.resumir(resultados, ev.Conjunto("v", 0.85, 0.80, ()))
    resumen["rutas_discrepantes"] = ["B"]
    texto = ev.resumen_markdown(ev.Evaluacion(resultados, resumen), [])
    assert "**NO cumple**" in texto
    assert "| B | 0,500 | 0,500 | z | b |" in texto
    assert "no coinciden: B" in texto


# ---------------------------------------------------------------------------
# Contra PostgreSQL
# ---------------------------------------------------------------------------
ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
TENANT = "org_test_calidad_cm"
TEI_URL = os.getenv("KC_EVAL_TEI_URL")


def _base_lista() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute("SELECT to_regproc('cm.refrescar_reachability')").fetchone()
            return bool(fila and fila[0])
    except psycopg.Error:
        return False


requiere_base = pytest.mark.skipif(
    not _base_lista(), reason="PostgreSQL no disponible o sin la migración 059 (make up && make db)"
)


@pytest.fixture(scope="module")
def evaluacion() -> ev.Evaluacion:
    embebedor = ev.ClienteTEI(TEI_URL) if TEI_URL else ev.EmbebedorReferencia()
    return ev.ejecutar(ADMIN_DSN, tenant=TENANT, embebedor=embebedor, conjunto=CONJUNTO)


@requiere_base
@pytest.mark.integracion
def test_las_40_preguntas_de_impacto_superan_los_umbrales(evaluacion: ev.Evaluacion) -> None:
    grafo = [r for r in evaluacion.resultados if r.operacion in ev.OPERACIONES_GRAFO]
    agregado = ev.resumir(grafo, CONJUNTO)
    assert agregado["precision"] > CONJUNTO.umbral_precision, agregado
    assert agregado["exhaustividad"] > CONJUNTO.umbral_exhaustividad, agregado


@requiere_base
@pytest.mark.integracion
def test_las_dos_rutas_de_impacto_responden_lo_mismo(evaluacion: ev.Evaluacion) -> None:
    assert evaluacion.resumen["estado_reachability"] == "vigente"
    assert evaluacion.resumen["rutas_discrepantes"] == []


@requiere_base
@pytest.mark.integracion
def test_ninguna_pregunta_empeora_respecto_a_la_linea_base(evaluacion: ev.Evaluacion) -> None:
    base = ev.cargar_linea_base()
    embebedor = "bge-m3" if TEI_URL else ev.EMBEBEDOR_REFERENCIA
    peores = ev.regresiones(evaluacion, base, embebedor)
    assert peores == [], "\n".join(peores)


@requiere_base
@pytest.mark.integracion
@pytest.mark.skipif(not TEI_URL, reason="KC_EVAL_TEI_URL sin definir: el criterio se mide con BGE-M3")
def test_las_50_preguntas_superan_los_umbrales_con_bge_m3(evaluacion: ev.Evaluacion) -> None:
    resumen = evaluacion.resumen
    assert resumen["cumple"], ev.resumen_markdown(evaluacion, [])
