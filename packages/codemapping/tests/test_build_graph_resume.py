"""Prueba de reanudación real de full_build() (KC-ONB-03-2).

No basta con que cm.repo_snapshots registre en qué fase quedó: hay que
comprobar que un segundo intento del MISMO commit no vuelve a ejecutar el
listado de archivos ni el parseo (fases 1-3) cuando el checkpoint local ya
las tiene cacheadas.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from kallicode_codemapping import build_graph
from kallicode_codemapping.config import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

PLATFORM_URL = "postgresql+psycopg://postgres:dev@localhost:5432/kallicode"
TENANT = "org_test_resume"


def _db_disponible() -> bool:
    try:
        create_engine(PLATFORM_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_disponible(), reason="Postgres no disponible (make up && make db)")


@pytest.fixture
def tenant_temporal() -> Iterator[str]:
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n) ON CONFLICT DO NOTHING"),
            {"id": TENANT, "n": "resume test"},
        )
    yield TENANT
    with engine.begin() as db:
        for tabla in (
            "vec.definition_embeddings",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.definitions",
            "cm.concepts",
            "cm.repo_snapshots",
        ):
            db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": TENANT})
        db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": TENANT})


@pytest.fixture
def repo_git(tmp_path) -> str:
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "a.py").write_text("def foo():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=origen, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=origen, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origen, check=True)
    subprocess.run(["git", "add", "-A"], cwd=origen, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=origen, check=True)
    return str(origen)


def _fake_semantica():
    def describe_symbols(settings, symbols):
        return [{"id": s.id, "description_en": "d", "role": "domain"} for s in symbols]

    def consolidate_definitions(settings, module, described_symbols):
        return [
            {
                "id": f"def-{module}",
                "name_en": f"Def {module}",
                "kind": "capability",
                "description_en": "d",
                "member_symbol_ids": [d["id"] for d in described_symbols],
            }
        ]

    def induce_ontology(settings, definitions, docs_excerpts):
        return {
            "concepts": [{"name_en": "T", "name_local": "T", "description_en": "x", "confidence": 0.9}],
            "relations": [],
        }

    def link_definitions(settings, definitions, concepts):
        return [{"definition_id": d["id"], "concept_name_en": "T", "confidence": 0.9} for d in definitions], []

    return describe_symbols, consolidate_definitions, induce_ontology, link_definitions


class FakeTEI:
    def __init__(self, base_url=None):
        pass

    def embed_texts_sync(self, texts):
        return [[0.0] * 1024 for _ in texts]


def test_resume_no_repite_fases_1_a_3(tenant_temporal, repo_git, tmp_path):
    workdir = str(tmp_path / "workdir")
    settings = Settings(
        tenant_id=tenant_temporal,
        repo_url=repo_git,
        workdir=workdir,
        store_database_url=PLATFORM_URL,
    )
    describe_symbols, consolidate_definitions, induce_ontology, link_definitions = _fake_semantica()

    real_walk_sources = build_graph.walk_sources
    contador_walk_sources = {"llamadas": 0}

    def walk_sources_contado(repo, settings):
        contador_walk_sources["llamadas"] += 1
        return real_walk_sources(repo, settings)

    # --- Primer intento: la fase 4 (LLM) falla a propósito ---
    with (
        patch.object(build_graph, "walk_sources", walk_sources_contado),
        patch.object(build_graph, "describe_symbols", side_effect=RuntimeError("crash simulado en fase 4")),
    ):
        with pytest.raises(RuntimeError, match="crash simulado"):
            build_graph.full_build(settings)

    assert contador_walk_sources["llamadas"] == 1

    engine = create_engine(PLATFORM_URL)
    with engine.connect() as db:
        snap = (
            db.execute(
                text("SELECT id, estado, fase_actual FROM cm.repo_snapshots WHERE tenant_id = :t"),
                {"t": tenant_temporal},
            )
            .mappings()
            .one()
        )
    assert snap["estado"] == "fallido"
    assert snap["fase_actual"] == "ingesta"  # llegó a confirmar ingesta antes de morir en semántica

    # --- Segundo intento: mismo commit, debe reanudar sin repetir fases 1-3 ---
    with (
        patch.object(build_graph, "walk_sources", walk_sources_contado),
        patch.object(build_graph, "describe_symbols", describe_symbols),
        patch.object(build_graph, "consolidate_definitions", consolidate_definitions),
        patch.object(build_graph, "induce_ontology", induce_ontology),
        patch.object(build_graph, "link_definitions", link_definitions),
        patch.object(build_graph, "TEIEmbeddingClient", FakeTEI),
    ):
        build_graph.full_build(settings)

    # walk_sources (fase 1) NO se volvió a llamar: se reanudó desde el checkpoint.
    assert contador_walk_sources["llamadas"] == 1

    with engine.connect() as db:
        snaps = (
            db.execute(text("SELECT id, estado FROM cm.repo_snapshots WHERE tenant_id = :t"), {"t": tenant_temporal})
            .mappings()
            .all()
        )
        n_files = db.execute(
            text("SELECT count(*) FROM cm.files WHERE tenant_id = :t"), {"t": tenant_temporal}
        ).scalar_one()

    # Mismo snapshot reutilizado (no se creó uno nuevo), ahora completo.
    assert len(snaps) == 1
    assert snaps[0]["id"] == snap["id"]
    assert snaps[0]["estado"] == "completo"
    assert n_files == 1
