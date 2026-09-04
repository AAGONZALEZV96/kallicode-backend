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

from config import settings
from embeddings import embed_and_upsert
from extract_sql import extract_schema, find_table_usage
from graph_loader import GraphLoader
from ingest_repo import sync_repo, walk_sources
from llm_enrich import (consolidate_definitions, describe_symbols,
                        induce_ontology, link_definitions)
from parser_treesitter import parse_file


def full_build():
    # Fase 1: repositorio
    repo, head = sync_repo()
    files = list(walk_sources(repo))
    print(f"[1/7] repo sincronizado: {len(files)} archivos @ {head[:10]}")

    # Fase 2: parsing determinista
    symbols = [sym for f in files for sym in parse_file(f)]
    print(f"[2/7] parseados {len(symbols)} simbolos")

    # Fase 3: esquema de datos + uso
    tables, fks, usage = [], [], []
    if settings.client_db_url:
        tables, fks = extract_schema()
        usage = find_table_usage(files, tables)
        print(f"[3/7] esquema: {len(tables)} tablas, {len(usage)} usos")

    # Fase 4: enriquecimiento LLM (cadena del par. 6.5)
    described = describe_symbols(symbols)
    by_module = defaultdict(list)
    for d, sym in zip(described, symbols):
        by_module[sym.file.split("/")[0]].append(d)
    definitions = [d for mod, syms in by_module.items()
                   for d in consolidate_definitions(mod, syms)]
    ontology = induce_ontology(definitions, docs_excerpts=[])
    links, curation_queue = link_definitions(definitions,
                                             ontology["concepts"])
    print(f"[4/7] {len(definitions)} definiciones, "
          f"{len(ontology['concepts'])} conceptos, "
          f"{len(curation_queue)} enlaces a curacion humana")

    # Fase 5: embeddings por capa
    by_id = {d["id"]: d for d in described}
    embed_and_upsert("code", [{
        "id": int(sym.id, 16) % (2**63),
        "text": f"{sym.name} {sym.signature} "
                f"{by_id.get(sym.id, {}).get('description_en', '')}",
        "payload": {"file": sym.file, "kind": sym.kind,
                    "name": sym.name, "layer": "code"},
    } for sym in symbols])
    embed_and_upsert("definitions", [{
        "id": abs(hash(d["id"])) % (2**63),
        "text": f"{d['name_en']} {d['description_en']}",
        "payload": {"definition_id": d["id"], "kind": d["kind"],
                    "layer": "definitions"},
    } for d in definitions])
    print("[5/7] embeddings cargados en Qdrant")

    # Fase 6: carga en Neo4j
    loader = GraphLoader()
    loader.setup()
    loader.load_files(files)
    loader.load_symbols(symbols)
    if tables:
        loader.load_schema(tables, fks, usage)
    loader.load_semantic(definitions, ontology, links)
    loader.set_last_sha(head)
    print("[6/7] grafo cargado en Neo4j")

    # Fase 7: cola de curacion (revision humana de la capa semantica)
    if curation_queue:
        import json
        with open("curation_queue.json", "w", encoding="utf-8") as fh:
            json.dump(curation_queue, fh, ensure_ascii=False, indent=2)
        print(f"[7/7] {len(curation_queue)} enlaces esperando curacion "
              "-> curation_queue.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if args.refresh:
        from refresh import run_refresh
        run_refresh()
    elif args.full:
        full_build()
    else:
        ap.print_help()
        sys.exit(1)
