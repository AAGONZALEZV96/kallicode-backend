"""Contrato versionado de estadísticas de ingesta (KC-CM-06-4).

Valida `cm.repo_snapshots.stats` contra `contratos/ingesta-stats-v1.schema.json`
usando el SnapshotManager real (sin BD: un cursor en memoria interpreta las
sentencias que usa) y comprueba que el código no se desvíe del contrato.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from kallicode_codemapping.snapshots import SnapshotManager
from psycopg.types.json import Jsonb

RAIZ = Path(__file__).resolve().parents[3]
SCHEMA_PATH = RAIZ / "contratos" / "ingesta-stats-v1.schema.json"
BUILD_GRAPH = (
    Path(__file__).resolve().parents[1] / "src" / "kallicode_codemapping" / "build_graph.py"
)

TENANT = "org_test"
TRACE = "trace-abc"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validador(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=FormatChecker())


class _BDEnMemoria:
    """Emula solo lo que SnapshotManager hace sobre cm.repo_snapshots.stats."""

    def __init__(self) -> None:
        self.stats: dict[str, Any] | None = None
        self.estado: str | None = None
        self._ultimo: Any = None

    def conexion(self) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        cur.execute.side_effect = self._execute
        cur.fetchone.side_effect = lambda: self._ultimo
        conn.cursor.return_value.__enter__.return_value = cur
        conn.cursor.return_value.__exit__.return_value = False
        return conn

    def _execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self._ultimo = None
        if "SET LOCAL" in sql:
            return
        if "SELECT id, estado, stats" in sql:
            return  # no hay snapshot previo
        if "SELECT stats FROM" in sql:
            self._ultimo = (copy.deepcopy(self.stats),) if self.stats is not None else None
            return
        jsonbs = [p for p in (params or ()) if isinstance(p, Jsonb)]
        if "INSERT INTO cm.repo_snapshots" in sql or "UPDATE cm.repo_snapshots" in sql:
            if jsonbs:
                self.stats = copy.deepcopy(jsonbs[0].obj)
            if "'iniciado'" in sql:
                self.estado = "iniciado"
            if "estado = 'fallido'" in sql:
                self.estado = "fallido"
            if "estado = 'vigente'" in sql:
                self.estado = "vigente"


@pytest.fixture
def bd() -> Any:
    memoria = _BDEnMemoria()
    with patch("psycopg.connect") as conectar:
        conectar.return_value.__enter__.return_value = memoria.conexion()
        yield memoria


def _gestor() -> SnapshotManager:
    return SnapshotManager(dsn="postgresql://localhost/db", tenant_id=TENANT)


def _errores(validador: Draft202012Validator, doc: Any) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validador.iter_errors(doc)
    ]


# --------------------------------------------------------------------------
# El contrato en sí
# --------------------------------------------------------------------------
def test_el_schema_es_json_schema_valido_y_esta_versionado(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == "urn:kallicode:contrato:ingesta-stats:v1"
    assert schema["x-version"].startswith("1.")


# --------------------------------------------------------------------------
# El SnapshotManager real produce documentos conformes
# --------------------------------------------------------------------------
def test_snapshot_recien_creado_cumple_el_contrato(
    bd: _BDEnMemoria, validador: Draft202012Validator
) -> None:
    _gestor().obtener_o_crear_snapshot("sha_12345678", "main", trace_id=TRACE)
    assert bd.stats is not None
    assert _errores(validador, bd.stats) == []
    assert bd.stats["checkpoint_fase"] == 0


def test_pipeline_completo_cumple_el_contrato(
    bd: _BDEnMemoria, validador: Draft202012Validator
) -> None:
    gestor = _gestor()
    snap_id, _, _ = gestor.obtener_o_crear_snapshot("sha_12345678", "main", trace_id=TRACE)
    gestor.registrar_checkpoint(
        snap_id, 3, {"files_count": 12, "symbols_count": 340, "tables_count": 4}, trace_id=TRACE
    )
    gestor.registrar_checkpoint(
        snap_id,
        5,
        {"definitions_count": 30, "concepts_count": 8, "links_count": 21},
        trace_id=TRACE,
    )
    gestor.registrar_checkpoint(
        snap_id, 6, {"code_vectors_count": 340, "def_vectors_count": 30}, trace_id=TRACE
    )
    gestor.marcar_completado(
        snap_id,
        {
            "files_persisted": 12,
            "symbols_persisted": 340,
            "definitions_persisted": 30,
            "concepts_persisted": 8,
            "links_persisted": 21,
            "vectors_persisted": 370,
            "curation_items_count": 5,
        },
        trace_id=TRACE,
    )
    assert _errores(validador, bd.stats) == []
    assert bd.stats["checkpoint_fase"] == 6
    assert [e["fase"] for e in bd.stats["events"]] == [
        "inicio",
        "fase_3",
        "fase_5",
        "fase_6",
        "finalizado",
    ]


def test_pipeline_fallido_cumple_el_contrato(
    bd: _BDEnMemoria, validador: Draft202012Validator
) -> None:
    gestor = _gestor()
    snap_id, _, _ = gestor.obtener_o_crear_snapshot("sha_12345678", "main", trace_id=TRACE)
    gestor.marcar_fallido(snap_id, "TEI no responde", fase=6, trace_id=TRACE)
    assert _errores(validador, bd.stats) == []
    assert bd.stats["error"] == {"fase": 6, "mensaje": "TEI no responde"}
    assert bd.stats["events"][-1]["level"] == "ERROR"


# --------------------------------------------------------------------------
# El contrato rechaza lo que debe rechazar
# --------------------------------------------------------------------------
def _valido() -> dict[str, Any]:
    return {
        "checkpoint_fase": 3,
        "trace_id": TRACE,
        "events": [],
        "fase_3": {"files_count": 1, "symbols_count": 2, "tables_count": 0},
    }


@pytest.mark.parametrize(
    ("mutar", "fragmento"),
    [
        (lambda d: d.pop("events"), "events"),
        (lambda d: d.pop("trace_id"), "trace_id"),
        (lambda d: d["fase_3"].__setitem__("files_count", -1), "files_count"),
        (lambda d: d["fase_3"].__setitem__("symbols_count", "2"), "symbols_count"),
        (lambda d: d["fase_3"].pop("tables_count"), "tables_count"),
        (lambda d: d.__setitem__("checkpoint_fase", 8), "checkpoint_fase"),
        (lambda d: d.__setitem__("campo_inventado", 1), "campo_inventado"),
        (lambda d: d.__setitem__("error", {"fase": 3}), "mensaje"),
    ],
)
def test_el_contrato_rechaza_documentos_invalidos(
    validador: Draft202012Validator, mutar: Any, fragmento: str
) -> None:
    doc = _valido()
    assert _errores(validador, doc) == []
    mutar(doc)
    errores = _errores(validador, doc)
    assert errores, "el documento mutado debía ser rechazado"
    assert any(fragmento in e for e in errores)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("level", "VERBOSE"),
        ("fase", "fase_9"),
        ("timestamp", "ayer"),
        ("data", "no-es-objeto"),
    ],
)
def test_el_contrato_rechaza_eventos_invalidos(
    validador: Draft202012Validator, campo: str, valor: Any
) -> None:
    evento = {
        "timestamp": "2026-10-14T12:00:00+00:00",
        "trace_id": TRACE,
        "tenant_id": TENANT,
        "snapshot_id": "snap_1",
        "fase": "fase_3",
        "level": "INFO",
        "mensaje": "ok",
        "data": {},
    }
    doc = _valido()
    doc["events"] = [evento]
    assert _errores(validador, doc) == []
    evento[campo] = valor
    assert _errores(validador, doc)


# --------------------------------------------------------------------------
# Anti-deriva: lo que build_graph.py escribe debe coincidir con el contrato
# --------------------------------------------------------------------------
def _llamadas_build_graph() -> list[ast.Call]:
    arbol = ast.parse(BUILD_GRAPH.read_text(encoding="utf-8"))
    return [n for n in ast.walk(arbol) if isinstance(n, ast.Call)]


def _claves_literales(nodo: ast.expr) -> set[str]:
    assert isinstance(nodo, ast.Dict), "se esperaba un dict literal"
    return {k.value for k in nodo.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def test_los_checkpoints_de_build_graph_coinciden_con_el_contrato(schema: dict[str, Any]) -> None:
    vistos: dict[int, set[str]] = {}
    for llamada in _llamadas_build_graph():
        if not (
            isinstance(llamada.func, ast.Attribute) and llamada.func.attr == "registrar_checkpoint"
        ):
            continue
        kw = {k.arg: k.value for k in llamada.keywords}
        fase = kw["fase"]
        assert isinstance(fase, ast.Constant)
        vistos[int(fase.value)] = _claves_literales(kw["stats_fase"])
    assert vistos, "no se encontró ningún registrar_checkpoint"
    for fase, claves in vistos.items():
        definido = schema["properties"][f"fase_{fase}"]
        assert claves == set(definido["properties"]), f"fase_{fase} desviada del contrato"
        assert claves == set(definido["required"])


def test_los_totales_de_build_graph_coinciden_con_el_contrato(schema: dict[str, Any]) -> None:
    claves: set[str] = set()
    for llamada in _llamadas_build_graph():
        if isinstance(llamada.func, ast.Attribute) and llamada.func.attr == "marcar_completado":
            claves |= _claves_literales({k.arg: k.value for k in llamada.keywords}["total_stats"])
    assert claves, "no se encontró marcar_completado"
    assert claves <= set(schema["properties"]), claves - set(schema["properties"])


@pytest.mark.xfail(
    strict=True,
    reason="build_graph.py:239 llama marcar_fallido(motivo=...) pero el parámetro se llama error_msg: "
    "TypeError en la rama de presupuesto insuficiente. Quitar el xfail al corregirlo.",
)
def test_las_llamadas_a_marcar_fallido_usan_la_firma_real() -> None:
    firma = inspect.signature(SnapshotManager.marcar_fallido)
    for llamada in _llamadas_build_graph():
        if isinstance(llamada.func, ast.Attribute) and llamada.func.attr == "marcar_fallido":
            nombres = {k.arg for k in llamada.keywords}
            assert nombres <= set(firma.parameters), nombres - set(firma.parameters)
