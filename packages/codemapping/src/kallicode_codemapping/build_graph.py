"""Orquestador del pipeline de construccion del grafo.

Uso:
    python build_graph.py --full       # construccion inicial completa
    python build_graph.py --refresh    # delega en refresh.py (diario)

Requiere (pip): tree-sitter-languages sqlalchemy openai FlagEmbedding
                qdrant-client neo4j
Y variables de entorno segun config.py (CM_CLIENT_ID, CM_REPO_URL,
NEO4J_*, QDRANT_URL, LLM_BASE_URL, CLIENT_DB_URL...).
"""

import argparse
import sys
from collections import defaultdict

from typing import Any


from .config import settings
from .embeddings import embed_and_upsert
from .extract_sql import extract_schema, find_table_usage
from .graph_loader import GraphLoader
from .ingest_repo import sync_repo, walk_sources
from .llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from .parser_treesitter import parse_file


def full_build() -> None:
    repo, head = sync_repo()
    files = list(walk_sources(repo))
    print(f"[1/7] repo sincronizado: {len(files)} archivos @ {head[:10]}")

    symbols = [sym for f in files for sym in parse_file(f)]
    print(f"[2/7] parseados {len(symbols)} simbolos")

    tables: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    usage: list[dict[str, str]] = []
    if settings.client_db_url:
        tables, fks = extract_schema()
        usage = find_table_usage(files, tables)
        print(f"[3/7] esquema: {len(tables)} tablas, {len(usage)} usos")

    described = describe_symbols(symbols)
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d, sym in zip(described, symbols, strict=True):
        by_module[sym.file.split("/")[0]].append(d)
    definitions = [d for mod, syms in by_module.items() for d in consolidate_definitions(mod, syms)]
    ontology = induce_ontology(definitions, docs_excerpts=[])
    links, curation_queue = link_definitions(definitions, ontology["concepts"])
    ...


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if args.refresh:
        # Import relativo correcto al paquete
        from .refresh import run_refresh
        run_refresh()
    elif args.full:
        full_build()
    else:
        ap.print_help()
        sys.exit(1)