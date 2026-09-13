"""Orquestador del pipeline de construccion del grafo.

Uso:
    python build_graph.py --full       # construccion inicial completa
    python build_graph.py --refresh    # delega en refresh.py (diario)

Requiere (pip): tree-sitter-languages sqlalchemy openai FlagEmbedding
                qdrant-client neo4j
Y variables de entorno segun config.py (tenant_id, repo_url, database_url,
embeddings_url, llm_base_url, llm_api_key...).
"""

import argparse
import sys
import uuid
from collections import defaultdict
from typing import Any

from .config import Settings, build_settings
from .embeddings import embed_and_upsert  # noqa: F401 (fase 5, pendiente SCRUM-3)
from .extract_sql import extract_schema, find_table_usage
from .graph_loader import GraphLoader  # noqa: F401 (fase 7, pendiente esquema cm)
from .ingest_repo import sync_repo, walk_sources
from .llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from .logging_setup import get_logger
from .parser_treesitter import parse_file

logger = get_logger()


def full_build(settings: Settings) -> None:
    trace_id = str(uuid.uuid4())
    ctx = {"trace_id": trace_id, "tenant_id": settings.tenant_id}

    repo, head = sync_repo(settings)
    files = list(walk_sources(repo, settings))
    logger.info(
        f"repo sincronizado: {len(files)} archivos @ {head[:10]}",
        extra={**ctx, "phase": "1/7", "file_count": len(files), "head": head},
    )

    symbols = [sym for f in files for sym in parse_file(f)]
    logger.info(
        f"parseados {len(symbols)} simbolos",
        extra={**ctx, "phase": "2/7", "symbol_count": len(symbols)},
    )

    tables: list[dict[str, Any]] = []
    usage: list[dict[str, str]] = []
    if settings.database_url:
        tables, _fks = extract_schema(settings)
        usage = find_table_usage(files, tables)
        logger.info(
            f"esquema: {len(tables)} tablas, {len(usage)} usos",
            extra={**ctx, "phase": "3/7", "table_count": len(tables), "usage_count": len(usage)},
        )

    described = describe_symbols(symbols)
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d, sym in zip(described, symbols, strict=True):
        by_module[sym.file.split("/")[0]].append(d)
    definitions = [d for mod, syms in by_module.items() for d in consolidate_definitions(mod, syms)]
    logger.info(
        f"definiciones consolidadas: {len(definitions)}",
        extra={**ctx, "phase": "4/7", "definition_count": len(definitions)},
    )

    ontology = induce_ontology(definitions, docs_excerpts=[])
    links, curation_queue = link_definitions(definitions, ontology["concepts"])
    logger.info(
        f"ontologia inducida, {len(links)} enlaces, {len(curation_queue)} en cola de curacion",
        extra={**ctx, "phase": "5/7", "link_count": len(links), "queue_size": len(curation_queue)},
    )

    logger.warning(
        "fase 6/7 (embeddings) pendiente: ver SCRUM-3 (KC-ONB-02-2)",
        extra={**ctx, "phase": "6/7"},
    )
    logger.warning(
        "fase 7/7 (persistencia en grafo) pendiente: ver SCRUM-16 (esquema cm)",
        extra={**ctx, "phase": "7/7"},
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if args.refresh:
        from .refresh import run_refresh
        run_refresh()
    elif args.full:
        settings = build_settings()
        full_build(settings)
    else:
        ap.print_help()
        sys.exit(1)
