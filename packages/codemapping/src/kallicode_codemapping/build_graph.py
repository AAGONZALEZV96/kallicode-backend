"""Orquestador del pipeline de construcción del grafo de conocimiento CodeMapping.

Uso:
    uv run python -m kallicode_codemapping.build_graph --full
    uv run python -m kallicode_codemapping.build_graph --refresh

Cubre las 7 fases del pipeline según ADR-018:
    1. Sincronización determinista del repositorio Git
    2. Extracción sintáctica AST con tree-sitter
    3. Introspección relacional y mapeo de tablas
    4. Enriquecimiento semántico con LLM (descripción, consolidación y ontología)
    5. Deduplicación y enlace semántico
    6. Vectorización densa (BGE-M3 vía TEI) con UUIDv5 deterministas (KC-ONB-02-2)
    7. Persistencia relacional en PostgreSQL 16 (esquemas cm y vec con COPY)
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
import uuid
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from . import checkpoints
from .config import Settings, build_settings
from .embeddings import TEIEmbeddingClient, generar_uuid5_vector
from .extract_sql import extract_schema, find_table_usage
from .graph_loader import GraphLoader
from .ingest_repo import SourceFile, sync_repo, walk_sources
from .llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from .logging_setup import get_logger
from .parser_treesitter import Symbol, parse_file

logger = get_logger()


def _rama_actual(repo: str) -> str:
    return (
        subprocess.run(
            ["git", "-C", repo, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        or "HEAD"
    )


def full_build(settings: Settings) -> None:
    """Ejecuta el pipeline completo de ingesta, enriquecimiento y vectorización.

    Cada corrida queda registrada en cm.repo_snapshots. Si muere a mitad de
    camino, el siguiente intento sobre el mismo commit reanuda la misma
    instantánea (KC-ONB-04-4): las fases 1-3 (ingesta determinista) se
    recuperan de un checkpoint en disco en lugar de recalcularse, y solo las
    fases posteriores (semántica, LLM) se vuelven a ejecutar.
    """
    trace_id = str(uuid.uuid4())
    repo, head = sync_repo(settings)
    rama = _rama_actual(repo)
    ctx = {
        "trace_id": trace_id,
        "tenant_id": settings.tenant_id,
        "head": head,
        "branch": rama,
    }

    with GraphLoader(dsn=settings.store_database_url, tenant_id=settings.tenant_id) as loader:
        snapshot_id = loader.start_snapshot(commit_sha=head, rama=rama)
        try:
            _full_build_con_snapshot(settings, loader, snapshot_id, repo, head, rama, ctx)
        except Exception as exc:
            loader.complete_snapshot(snapshot_id, estado="fallido", motivo=str(exc))
            raise


def _full_build_con_snapshot(
    settings: Settings,
    loader: GraphLoader,
    snapshot_id: str,
    repo: str,
    head: str,
    rama: str,
    ctx: dict[str, Any],
) -> None:
    # -------------------------------------------------------------
    # Fases 1-3: ingesta determinista (repo, AST, esquema) -- con checkpoint
    # -------------------------------------------------------------
    cache = checkpoints.cargar(settings.workdir, settings.tenant_id, snapshot_id, "ingesta")
    if cache is not None:
        files = [SourceFile(**f) for f in cache["files"]]
        symbols = [Symbol(**s) for s in cache["symbols"]]
        tables: list[dict[str, Any]] = cache["tables"]
        fks: list[dict[str, Any]] = cache["fks"]
        usage: list[dict[str, str]] = cache["usage"]
        logger.info(
            "fases 1-3: reanudadas desde checkpoint local",
            extra={
                **ctx,
                "phase": "1-3/7 (cache)",
                "file_count": len(files),
                "symbol_count": len(symbols),
            },
        )
    else:
        files = list(walk_sources(repo, settings))
        logger.info(
            f"repo sincronizado @ {head[:10]} (rama: {rama})",
            extra={**ctx, "phase": "1/7", "file_count": len(files)},
        )

        symbols = [sym for f in files for sym in parse_file(f)]
        logger.info(
            f"parseados {len(symbols)} simbolos",
            extra={**ctx, "phase": "2/7", "symbol_count": len(symbols)},
        )

        tables, fks, usage = [], [], []
        if settings.database_url:
            tables, fks = extract_schema(settings)
            usage = find_table_usage(files, tables)
            logger.info(
                f"esquema: {len(tables)} tablas, {len(fks)} fks, {len(usage)} usos",
                extra={
                    **ctx,
                    "phase": "3/7",
                    "table_count": len(tables),
                    "usage_count": len(usage),
                },
            )

        checkpoints.guardar(
            settings.workdir,
            settings.tenant_id,
            snapshot_id,
            "ingesta",
            {
                "files": [dataclasses.asdict(f) for f in files],
                "symbols": [dataclasses.asdict(s) for s in symbols],
                "tables": tables,
                "fks": fks,
                "usage": usage,
            },
        )

    file_ids = loader.load_files(snapshot_id, files)
    loader.load_symbols(symbols, file_ids)
    if tables:
        loader.load_schema(tables, fks, usage, file_ids)
    loader.marcar_fase(snapshot_id, "ingesta")

    # -------------------------------------------------------------
    # Fases 4-5: Enriquecimiento semántico con LLM
    # -------------------------------------------------------------
    described = describe_symbols(settings, symbols)
    described_by_id: dict[str, dict[str, Any]] = {str(d["id"]): d for d in described if "id" in d}

    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym in symbols:
        desc_item = described_by_id.get(sym.id)
        if desc_item:
            posix_path = PurePosixPath(sym.file.replace("\\", "/"))
            modulo = posix_path.parts[0] if posix_path.parts else "root"
            by_module[modulo].append(desc_item)

    definitions = [
        d for mod, syms in by_module.items() for d in consolidate_definitions(settings, mod, syms)
    ]
    logger.info(
        f"definiciones consolidadas: {len(definitions)}",
        extra={**ctx, "phase": "4/7", "definition_count": len(definitions)},
    )

    ontology = induce_ontology(settings, definitions, docs_excerpts=[])
    links, curation_queue = link_definitions(settings, definitions, ontology.get("concepts", []))
    concepts = ontology.get("concepts", [])
    logger.info(
        f"ontologia inducida: {len(links)} enlaces, {len(curation_queue)} en cola de curacion",
        extra={
            **ctx,
            "phase": "5/7",
            "link_count": len(links),
            "queue_size": len(curation_queue),
        },
    )

    # -------------------------------------------------------------
    # Fase 6: Generación de embeddings vía TEI con UUIDv5 (KC-ONB-02-2)
    # -------------------------------------------------------------
    tei_client = TEIEmbeddingClient(base_url=settings.tei_endpoint_url)

    textos_simbolos: list[str] = [
        f"{sym.name} {sym.signature} {described_by_id.get(sym.id, {}).get('description_en', '')}".strip()
        for sym in symbols
    ]
    items_codigo: list[dict[str, Any]] = []
    if textos_simbolos:
        logger.info(
            f"generando embeddings TEI para {len(textos_simbolos)} simbolos",
            extra={**ctx, "phase": "6/7", "count": len(textos_simbolos)},
        )
        vectores_simbolos = tei_client.embed_texts_sync(textos_simbolos)
        for sym, texto, vector in zip(symbols, textos_simbolos, vectores_simbolos, strict=True):
            items_codigo.append(
                {
                    "id": str(generar_uuid5_vector(settings.tenant_id, "code", sym.id)),
                    "tenant_id": settings.tenant_id,
                    "layer": "code",
                    "node_ref": sym.id,
                    "text": texto,
                    "vector": vector,
                    "payload": {
                        "file": sym.file,
                        "kind": sym.kind,
                        "name": sym.name,
                        "module": (
                            PurePosixPath(sym.file.replace("\\", "/")).parts[0]
                            if PurePosixPath(sym.file.replace("\\", "/")).parts
                            else "root"
                        ),
                    },
                }
            )

    textos_defs: list[str] = [
        f"{d['name_en']} {d.get('description_en', '')}".strip()
        for d in definitions
        if "name_en" in d and "id" in d
    ]
    items_defs: list[dict[str, Any]] = []
    if textos_defs:
        logger.info(
            f"generando embeddings TEI para {len(textos_defs)} definiciones",
            extra={**ctx, "phase": "6/7", "count": len(textos_defs)},
        )
        vectores_defs = tei_client.embed_texts_sync(textos_defs)
        for d, texto, vector in zip(definitions, textos_defs, vectores_defs, strict=True):
            items_defs.append(
                {
                    "id": str(generar_uuid5_vector(settings.tenant_id, "definitions", d["id"])),
                    "tenant_id": settings.tenant_id,
                    "layer": "definitions",
                    "node_ref": d["id"],
                    "text": texto,
                    "vector": vector,
                    "payload": {"kind": d.get("kind", "module")},
                }
            )

    logger.info(
        f"embeddings TEI completados: {len(items_codigo)} simbolos, {len(items_defs)} definiciones",
        extra={
            **ctx,
            "phase": "6/7",
            "code_vectors_count": len(items_codigo),
            "def_vectors_count": len(items_defs),
        },
    )

    loader.marcar_fase(snapshot_id, "semantica")

    # -------------------------------------------------------------
    # Fase 7: Persistencia semántica y vectorial (ADR-018)
    # -------------------------------------------------------------
    loader.load_semantic(definitions, ontology, links)
    loader.upsert_vectors(items_codigo + items_defs)
    loader.marcar_fase(snapshot_id, "vectores")

    loader.complete_snapshot(snapshot_id, estado="completo")
    checkpoints.limpiar(settings.workdir, settings.tenant_id, snapshot_id)

    logger.info(
        f"fase 7/7: grafo persistido @ {head[:10]}",
        extra={
            **ctx,
            "phase": "7/7",
            "files_persisted": len(files),
            "symbols_persisted": len(symbols),
            "definitions_persisted": len(definitions),
            "concepts_persisted": len(concepts),
            "links_persisted": len(links),
            "vectors_persisted": len(items_codigo) + len(items_defs),
            "curation_items_count": len(curation_queue),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Construcción o refresco del grafo de conocimiento de CodeMapping."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ejecuta la construcción completa desde cero.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ejecuta la actualización incremental diaria delegando en refresh.py.",
    )
    args = parser.parse_args()

    if args.refresh:
        from .refresh import run_refresh

        run_refresh()
    elif args.full:
        app_settings = build_settings()
        full_build(app_settings)
    else:
        parser.print_help()
        sys.exit(1)
