"""full_build(alcance="determinista"): la reconstrucción sin LLM ni TEI (KC-CM-12-1), sin base de datos."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping import build_graph

from .test_build_graph_unit import SNAP, _Entorno, _settings


@pytest.fixture
def entorno() -> Any:
    env = _Entorno()
    with ExitStack() as stack:
        env.aplicar(stack)
        yield env


def test_el_alcance_determinista_no_usa_llm_ni_tei_ni_toca_la_capa_semantica(
    entorno: _Entorno, tmp_path: Path
) -> None:
    # Con la base del cliente configurada, el esquema también se extrae y se carga.
    cfg = _settings(tmp_path, database_url="postgresql://cliente:x@localhost:5432/cliente")
    build_graph.full_build(cfg, alcance="determinista")

    # Capa determinista: se carga y se confirma.
    entorno.loader.load_files.assert_called_once()
    entorno.loader.load_symbols.assert_called_once()
    entorno.loader.load_schema.assert_called_once()
    # Nada de LLM, TEI, presupuesto de tokens, documentos ni capa semántica.
    for doble in (
        entorno.describe,
        entorno.consolidar,
        entorno.ontologia,
        entorno.enlaces,
        entorno.verificar,
        entorno.ingesta_docs,
        entorno.reenlazar,
        entorno.nuggets,
        entorno.tei.embed_texts_sync,
    ):
        doble.assert_not_called()
    entorno.loader.load_semantic.assert_not_called()
    entorno.loader.load_curation_queue.assert_not_called()
    entorno.loader.upsert_vectors.assert_not_called()
    entorno.loader.actualizar_descripciones_simbolos.assert_not_called()


def test_el_alcance_determinista_cierra_el_snapshot_como_completo(
    entorno: _Entorno, tmp_path: Path
) -> None:
    build_graph.full_build(_settings(tmp_path), alcance="determinista")

    assert [c.args[1] for c in entorno.loader.marcar_fase.call_args_list] == ["ingesta", "completo"]
    entorno.loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")
    totales = entorno.snap_mgr.marcar_completado.call_args.kwargs["total_stats"]
    assert totales["files_persisted"] == 1 and totales["symbols_persisted"] == 1
    entorno.snap_mgr.marcar_fallido.assert_not_called()


def test_recalcula_la_alcanzabilidad(entorno: _Entorno, tmp_path: Path) -> None:
    build_graph.full_build(_settings(tmp_path), alcance="determinista")

    entorno.loader.fase.assert_called_with("reachability")
    consultas = [
        c.args[0]
        for c in entorno.loader.fase.return_value.__enter__.return_value.execute.call_args_list
    ]
    assert any("cm.refrescar_reachability" in q for q in consultas)


def test_si_la_alcanzabilidad_falla_la_reconstruccion_no_falla(
    entorno: _Entorno, tmp_path: Path
) -> None:
    """Es derivada: las consultas caen al recorrido recursivo y el refresco diario la repone."""
    entorno.loader.fase.side_effect = RuntimeError("sin cm.reachability")

    build_graph.full_build(_settings(tmp_path), alcance="determinista")

    entorno.loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")


def test_alcance_invalido(entorno: _Entorno, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="alcance"):
        build_graph.full_build(_settings(tmp_path), alcance="parcial")
    entorno.snap_mgr.obtener_o_crear_snapshot.assert_not_called()


def test_el_alcance_completo_sigue_siendo_el_de_siempre(entorno: _Entorno, tmp_path: Path) -> None:
    build_graph.full_build(_settings(tmp_path))

    entorno.describe.assert_called_once()
    entorno.loader.load_semantic.assert_called_once()
    assert "alcance" not in entorno.snap_mgr.marcar_completado.call_args.kwargs["total_stats"]
