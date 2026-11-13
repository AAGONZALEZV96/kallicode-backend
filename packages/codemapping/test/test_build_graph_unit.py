"""Pruebas unitarias del orquestador full_build() sin base de datos (KC-CM-06-6).

`tests/test_build_graph_resume.py` ejercita el pipeline contra Postgres real y
se omite cuando no hay base. Aquí se reemplazan las fronteras (git, Postgres,
LLM, TEI) por dobles para que la lógica de orquestación -- fases, checkpoints,
presupuesto, manejo de fallos -- se verifique siempre, también en local.
"""

from __future__ import annotations

import dataclasses
import runpy
import subprocess
import sys
import warnings
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kallicode_codemapping import build_graph, checkpoints
from kallicode_codemapping.budget import BudgetExceededError
from kallicode_codemapping.config import Settings, build_settings
from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.parser_treesitter import Symbol

TENANT = "org_unit"
SNAP = "snap-1"
HEAD = "abcdef1234567890"


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return build_settings(
        tenant_id=TENANT, repo_url="https://example.com/r.git", workdir=str(tmp_path), **extra
    )


def _fuente() -> SourceFile:
    return SourceFile(path="app/mod.py", language="python", sha256="h", content="def f(): pass\n")


def _simbolo(sid: str = "s1", archivo: str = "app/mod.py") -> Symbol:
    return Symbol(
        id=sid,
        file=archivo,
        language="python",
        kind="function",
        name="f",
        start_line=1,
        end_line=1,
        signature="def f()",
        snippet="def f(): pass",
    )


class _Entorno:
    """Dobles de todas las fronteras de full_build, configurables por prueba."""

    def __init__(self, simbolos: list[Symbol] | None = None) -> None:
        self.simbolos = [_simbolo()] if simbolos is None else simbolos
        self.snap_mgr = MagicMock(name="SnapshotManager")
        self.snap_mgr.obtener_o_crear_snapshot.return_value = (SNAP, "iniciado", None)
        self.snap_mgr.obtener_checkpoint.return_value = 0
        self.loader = MagicMock(name="GraphLoader")
        self.loader.__enter__.return_value = self.loader
        self.loader.__exit__.return_value = False
        self.tei = MagicMock(name="TEI")
        self.tei.embed_texts_sync.side_effect = lambda textos: [[0.0] * 4 for _ in textos]
        self.walk = MagicMock(side_effect=lambda *_a, **_k: iter([_fuente()]))
        self.parse = MagicMock(side_effect=lambda *_a, **_k: iter([self.simbolos]))
        self.presupuesto = MagicMock(
            total_tokens_estimados=100, costo_usd_estimado=0.01, cuota_disponible=5000
        )
        self.verificar = MagicMock(return_value=self.presupuesto)
        self.describe = MagicMock(
            side_effect=lambda cfg, simbolos: [
                {"id": s.id, "description_en": "desc", "role": "domain"} for s in simbolos
            ]
        )
        self.consolidar = MagicMock(
            side_effect=lambda cfg, modulo, descritos: [
                {
                    "id": f"def-{modulo}",
                    "name_en": f"Def {modulo}",
                    "kind": "capability",
                    "description_en": "d",
                }
            ]
        )
        self.ontologia = MagicMock(
            return_value={"concepts": [{"name_en": "T"}], "relations": []},
        )
        self.enlaces = MagicMock(return_value=([{"definition_id": "def-app"}], [{"q": 1}]))
        self.extract_schema = MagicMock(return_value=([{"name": "t"}], [{"fk": 1}]))
        self.table_usage = MagicMock(return_value=[{"file": "app/mod.py", "table": "t"}])
        # Paso 5/5 de documentación (KC-CM-08-1): uno procesado, uno fallido, 2 extractos.
        self.ingesta_docs = MagicMock(
            return_value=[
                MagicMock(estado="procesado", documento_id="doc-ok"),
                MagicMock(estado="fallido", documento_id="doc-ko"),
            ]
        )
        # Propuesta de nuggets (KC-CM-08-2): (resultado, nuevos guardados).
        self.nuggets = MagicMock(return_value=(MagicMock(), 3))
        self.loader.obtener_extractos_docs.return_value = ["extracto uno", "extracto dos"]
        self.reenlazar = MagicMock(return_value=7)
        # Presupuesto documental (KC-CM-08-6)
        self.control_docs = MagicMock(name="ControlPresupuestoDocs", excedido=False)
        self.construir_control = MagicMock(return_value=self.control_docs)
        self.registrar_docs = MagicMock(return_value={})

    def aplicar(self, stack: ExitStack) -> None:
        mod = "kallicode_codemapping.build_graph"
        pares = {
            "sync_repo": MagicMock(return_value=("/repo", HEAD, False)),
            "_rama_actual": MagicMock(return_value="main"),
            "SnapshotManager": MagicMock(return_value=self.snap_mgr),
            "GraphLoader": MagicMock(return_value=self.loader),
            "TEIEmbeddingClient": MagicMock(return_value=self.tei),
            "walk_sources": self.walk,
            "parsear_fuentes_en_streaming": self.parse,
            "verificar_presupuesto_tenant": self.verificar,
            "describe_symbols": self.describe,
            "consolidate_definitions": self.consolidar,
            "induce_ontology": self.ontologia,
            "link_definitions": self.enlaces,
            "extract_schema": self.extract_schema,
            "find_table_usage": self.table_usage,
            "procesar_documentos_pendientes": self.ingesta_docs,
            "reenlazar_documentos": self.reenlazar,
            "construir_control_docs": self.construir_control,
            "registrar_resultado_documental": self.registrar_docs,
            "proponer_nuggets": self.nuggets,
        }
        for nombre, doble in pares.items():
            stack.enter_context(patch(f"{mod}.{nombre}", doble))


@pytest.fixture
def entorno() -> Any:
    env = _Entorno()
    with ExitStack() as stack:
        env.aplicar(stack)
        yield env


def _checkpoints_registrados(env: _Entorno) -> dict[int, dict[str, int]]:
    return {
        c.kwargs["fase"]: c.kwargs["stats_fase"]
        for c in env.snap_mgr.registrar_checkpoint.call_args_list
    }


# --------------------------------------------------------------------------
# Camino feliz
# --------------------------------------------------------------------------
def test_full_build_completa_las_siete_fases(entorno: _Entorno, tmp_path: Path) -> None:
    build_graph.full_build(_settings(tmp_path))

    assert _checkpoints_registrados(entorno) == {
        3: {"files_count": 1, "symbols_count": 1, "tables_count": 0},
        5: {"definitions_count": 1, "concepts_count": 1, "links_count": 1},
        6: {"code_vectors_count": 1, "def_vectors_count": 1},
    }
    fases = [c.args[1] for c in entorno.loader.marcar_fase.call_args_list]
    assert fases == ["ingesta", "semantica", "vectores", "completo"]
    entorno.loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")
    totales = entorno.snap_mgr.marcar_completado.call_args.kwargs["total_stats"]
    assert totales == {
        "files_persisted": 1,
        "symbols_persisted": 1,
        "definitions_persisted": 1,
        "concepts_persisted": 1,
        "links_persisted": 1,
        "vectors_persisted": 2,
        "curation_items_count": 1,
    }
    entorno.snap_mgr.marcar_fallido.assert_not_called()


def test_documentacion_alimenta_la_ontologia_y_se_reenlaza(
    entorno: _Entorno, tmp_path: Path
) -> None:
    """KC-CM-08-1: induce_ontology ya no recibe docs_excerpts=[] fijo."""
    cfg = _settings(tmp_path)
    estado_al_reenlazar: dict[str, bool] = {}

    def reenlazar(*_a: Any, **_k: Any) -> int:
        estado_al_reenlazar["vectores"] = entorno.loader.upsert_vectors.called
        estado_al_reenlazar["cerrado"] = entorno.loader.complete_snapshot.called
        return 7

    entorno.reenlazar.side_effect = reenlazar
    build_graph.full_build(cfg)

    entorno.ingesta_docs.assert_called_once()
    args, kwargs = entorno.ingesta_docs.call_args
    assert args[0] is entorno.loader and args[3] == cfg.tenant_id
    assert kwargs["control"] is entorno.control_docs
    assert kwargs["tamano_max_caracteres"] == cfg.docs_chunk_max_chars
    assert kwargs["solape_caracteres"] == cfg.docs_chunk_overlap_chars
    entorno.loader.obtener_extractos_docs.assert_called_once_with(
        limite=cfg.docs_ontology_max_excerpts
    )
    assert entorno.ontologia.call_args.kwargs["docs_excerpts"] == ["extracto uno", "extracto dos"]

    # El re-enlace corre tras persistir vectores y antes de cerrar el snapshot.
    entorno.reenlazar.assert_called_once()
    assert estado_al_reenlazar == {"vectores": True, "cerrado": False}


def test_presupuesto_documental_se_construye_y_sus_stats_van_al_snapshot(
    entorno: _Entorno, tmp_path: Path
) -> None:
    """KC-CM-08-6: la fase documental lleva su propio control, stats y consumo."""
    cfg = _settings(tmp_path)
    build_graph.full_build(cfg)

    entorno.construir_control.assert_called_once_with(entorno.loader, cfg)
    entorno.registrar_docs.assert_called_once()
    args, kwargs = entorno.registrar_docs.call_args
    assert args == (entorno.loader, SNAP, entorno.control_docs)
    assert kwargs["resultados"] == entorno.ingesta_docs.return_value


def test_superar_el_presupuesto_documental_no_invalida_el_resto_del_grafo(
    entorno: _Entorno, tmp_path: Path
) -> None:
    """KC-CM-08-6: el corte presupuestario detiene solo la fase documental."""
    entorno.control_docs.excedido = True
    entorno.ingesta_docs.return_value = []  # nada alcanzó a procesarse

    build_graph.full_build(_settings(tmp_path))

    entorno.loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")
    entorno.snap_mgr.marcar_fallido.assert_not_called()
    entorno.ontologia.assert_called_once()  # la ontología y el resto de las fases siguieron


def test_propone_nuggets_solo_de_los_documentos_procesados_en_la_corrida(
    entorno: _Entorno, tmp_path: Path
) -> None:
    """KC-CM-08-2: tras la ingesta, los documentos procesados alimentan la propuesta de nuggets."""
    cfg = _settings(tmp_path, nuggets_max_chunks_por_lote=4, nuggets_max_caracteres_por_lote=5000)
    build_graph.full_build(cfg)

    entorno.nuggets.assert_called_once_with(
        entorno.loader,
        TENANT,
        documento_ids=["doc-ok"],
        max_chunks_por_lote=4,
        max_caracteres_por_lote=5000,
    )


@pytest.mark.parametrize(
    ("extra", "resultados"),
    [
        ({"nuggets_enabled": False}, None),
        ({}, [MagicMock(estado="fallido", documento_id="doc-ko")]),
    ],
    ids=["desactivado", "sin-documentos-procesados"],
)
def test_no_propone_nuggets_si_esta_desactivado_o_no_hay_documentos(
    entorno: _Entorno, tmp_path: Path, extra: dict[str, Any], resultados: Any
) -> None:
    if resultados is not None:
        entorno.ingesta_docs.return_value = resultados
    build_graph.full_build(_settings(tmp_path, **extra))
    entorno.nuggets.assert_not_called()


def test_los_vectores_usan_uuid5_deterministas_y_modulo_del_archivo(
    entorno: _Entorno, tmp_path: Path
) -> None:
    build_graph.full_build(_settings(tmp_path))

    items = entorno.loader.upsert_vectors.call_args.args[0]
    codigo = next(i for i in items if i["layer"] == "code")
    definicion = next(i for i in items if i["layer"] == "definitions")
    assert codigo["payload"]["module"] == "app"
    assert codigo["node_ref"] == "s1"
    assert definicion["node_ref"] == "def-app"
    assert len({i["id"] for i in items}) == 2

    entorno.loader.upsert_vectors.reset_mock()
    build_graph.full_build(_settings(tmp_path))
    repetido = entorno.loader.upsert_vectors.call_args.args[0]
    assert [i["id"] for i in repetido] == [i["id"] for i in items]


def test_el_checkpoint_en_disco_se_limpia_al_completar(entorno: _Entorno, tmp_path: Path) -> None:
    build_graph.full_build(_settings(tmp_path))
    assert checkpoints.cargar(str(tmp_path), TENANT, SNAP, "ingesta") is None


def test_con_base_del_cliente_extrae_el_esquema_y_lo_persiste(
    entorno: _Entorno, tmp_path: Path
) -> None:
    cfg = _settings(tmp_path, database_url="postgresql://u:p@cliente.example/db")
    build_graph.full_build(cfg)

    entorno.extract_schema.assert_called_once_with(cfg)
    entorno.loader.load_schema.assert_called_once()
    assert _checkpoints_registrados(entorno)[3]["tables_count"] == 1


def test_sin_base_del_cliente_no_se_toca_el_esquema(entorno: _Entorno, tmp_path: Path) -> None:
    build_graph.full_build(_settings(tmp_path))
    entorno.extract_schema.assert_not_called()
    entorno.loader.load_schema.assert_not_called()


def test_sin_simbolos_no_se_llama_a_tei_para_codigo(tmp_path: Path) -> None:
    env = _Entorno(simbolos=[])
    env.consolidar.side_effect = lambda *_a, **_k: []
    with ExitStack() as stack:
        env.aplicar(stack)
        build_graph.full_build(_settings(tmp_path))

    env.tei.embed_texts_sync.assert_not_called()
    assert _checkpoints_registrados(env)[6] == {"code_vectors_count": 0, "def_vectors_count": 0}


def test_archivo_en_la_raiz_usa_el_modulo_de_su_primer_segmento(tmp_path: Path) -> None:
    env = _Entorno(simbolos=[_simbolo("s9", archivo="main.py")])
    with ExitStack() as stack:
        env.aplicar(stack)
        build_graph.full_build(_settings(tmp_path))
    env.consolidar.assert_called_once()
    assert env.consolidar.call_args.args[1] == "main.py"


# --------------------------------------------------------------------------
# Reanudación desde checkpoint
# --------------------------------------------------------------------------
def test_reanuda_fases_1_a_3_desde_el_checkpoint_local(entorno: _Entorno, tmp_path: Path) -> None:
    checkpoints.guardar(
        str(tmp_path),
        TENANT,
        SNAP,
        "ingesta",
        {
            "files": [dataclasses.asdict(_fuente())],
            "symbols": [dataclasses.asdict(_simbolo())],
            "tables": [],
            "fks": [],
            "usage": [],
        },
    )
    entorno.snap_mgr.obtener_checkpoint.return_value = 3

    build_graph.full_build(_settings(tmp_path))

    entorno.walk.assert_not_called()
    entorno.parse.assert_not_called()
    assert 3 in _checkpoints_registrados(entorno)
    entorno.loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")


# --------------------------------------------------------------------------
# Fallos
# --------------------------------------------------------------------------
def test_un_error_del_pipeline_marca_el_snapshot_fallido_y_se_propaga(
    entorno: _Entorno, tmp_path: Path
) -> None:
    entorno.describe.side_effect = RuntimeError("LLM caído")

    with pytest.raises(RuntimeError, match="LLM caído"):
        build_graph.full_build(_settings(tmp_path))

    entorno.snap_mgr.marcar_fallido.assert_called_once_with(
        SNAP,
        "LLM caído",
        fase=0,
        trace_id=entorno.snap_mgr.marcar_fallido.call_args.kwargs["trace_id"],
    )
    entorno.loader.complete_snapshot.assert_called_once_with(
        SNAP, estado="fallido", motivo="LLM caído"
    )


def test_presupuesto_insuficiente_aborta_antes_del_enriquecimiento(
    entorno: _Entorno, tmp_path: Path
) -> None:
    entorno.verificar.side_effect = BudgetExceededError("cuota agotada")

    with pytest.raises(BudgetExceededError):
        build_graph.full_build(_settings(tmp_path))

    entorno.describe.assert_not_called()
    entorno.loader.complete_snapshot.assert_any_call(SNAP, estado="fallido", motivo="cuota agotada")


# --------------------------------------------------------------------------
# _rama_actual y CLI
# --------------------------------------------------------------------------
def _repo_git(ruta: Path) -> str:
    ruta.mkdir()
    for cmd in (
        ["git", "init", "-q", "-b", "trabajo"],
        ["git", "config", "user.email", "t@t.local"],
        ["git", "config", "user.name", "t"],
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=ruta, check=True)
    return str(ruta)


def test_rama_actual_devuelve_la_rama_del_repo(tmp_path: Path) -> None:
    assert build_graph._rama_actual(_repo_git(tmp_path / "r")) == "trabajo"


def test_rama_actual_en_cabeza_desacoplada_devuelve_head(tmp_path: Path) -> None:
    repo = _repo_git(tmp_path / "r")
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", repo, "checkout", "-q", sha], check=True, capture_output=True)
    assert build_graph._rama_actual(repo) == "HEAD"


def _ejecutar_cli(*argv: str) -> None:
    with warnings.catch_warnings(), patch.object(sys, "argv", ["build_graph", *argv]):
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("kallicode_codemapping.build_graph", run_name="__main__")


def test_cli_sin_argumentos_imprime_ayuda_y_sale_con_1(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as salida:
        _ejecutar_cli()
    assert salida.value.code == 1
    assert "--full" in capsys.readouterr().out


def test_cli_refresh_delega_en_run_refresh() -> None:
    with patch("kallicode_codemapping.refresh.run_refresh") as refresco:
        _ejecutar_cli("--refresh")
    refresco.assert_called_once_with()
