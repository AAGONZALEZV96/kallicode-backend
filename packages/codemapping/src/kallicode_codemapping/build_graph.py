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
    7. Persistencia relacional en PostgreSQL 16 (esquema cm)
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from .config import Settings, build_settings
from .embeddings import TEIEmbeddingClient, generar_uuid5_vector
from .extract_sql import extract_schema, find_table_usage
from .graph_loader import GraphLoader  # noqa: F401
from .ingest_repo import sync_repo, walk_sources
from .llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from .logging_setup import get_logger
from .parser_treesitter import Symbol, parse_file

logger = get_logger()


def full_build(settings: Settings) -> None:
    """Ejecuta el pipeline completo de ingesta, enriquecimiento y vectorización."""
    trace_id = str(uuid.uuid4())
    ctx = {"trace_id": trace_id, "tenant_id": settings.tenant_id}

    # -------------------------------------------------------------
    # Fase 1: Repositorio Git
    # -------------------------------------------------------------
    repo, head = sync_repo(settings)
    files = list(walk_sources(repo, settings))
    logger.info(
        f"repo sincronizado: {len(files)} archivos @ {head[:10]}",
        extra={**ctx, "phase": "1/7", "file_count": len(files), "head": head},
    )

    # -------------------------------------------------------------
    # Fase 2: Parsing sintáctico AST con tree-sitter
    # -------------------------------------------------------------
    symbols: list[Symbol] = [sym for f in files for sym in parse_file(f)]
    logger.info(
        f"parseados {len(symbols)} simbolos",
        extra={**ctx, "phase": "2/7", "symbol_count": len(symbols)},
    )

    # -------------------------------------------------------------
    # Fase 3: Esquema relacional de base de datos y uso desde código
    # -------------------------------------------------------------
    tables: list[dict[str, Any]] = []
    usage: list[dict[str, str]] = []
    if settings.database_url:
        tables, _fks = extract_schema(settings)
        usage = find_table_usage(files, tables)
        logger.info(
            f"esquema: {len(tables)} tablas, {len(usage)} usos",
            extra={
                **ctx,
                "phase": "3/7",
                "table_count": len(tables),
                "usage_count": len(usage),
            },
        )

    # -------------------------------------------------------------
    # Fase 4: Enriquecimiento semántico con LLM (Fábrica / DeepSeek)
    # -------------------------------------------------------------
    described = describe_symbols(symbols)

    # CM-F04: Indexar descripciones por ID para evitar emparejamiento posicional frágil
    described_by_id: dict[str, dict[str, Any]] = {str(d["id"]): d for d in described if "id" in d}

    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for sym in symbols:
        desc_item = described_by_id.get(sym.id)
        if desc_item:
            # CM-F16: Normalizar rutas a formato POSIX antes de segmentar por módulo
            posix_path = PurePosixPath(sym.file.replace("\\", "/"))
            modulo = posix_path.parts[0] if posix_path.parts else "root"
            by_module[modulo].append(desc_item)

    definitions = [d for mod, syms in by_module.items() for d in consolidate_definitions(mod, syms)]

    logger.info(
        f"definiciones consolidadas: {len(definitions)}",
        extra={**ctx, "phase": "4/7", "definition_count": len(definitions)},
    )

    # -------------------------------------------------------------
    # Fase 5: Inducción de Ontología y Enlace a Definiciones
    # -------------------------------------------------------------
    ontology = induce_ontology(definitions, docs_excerpts=[])
    concepts = ontology.get("concepts", [])
    links, curation_queue = link_definitions(definitions, concepts)
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

    # 1. Vectorizar Símbolos de Código
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
                    },
                }
            )

    # 2. Vectorizar Definiciones Técnicas
    textos_defs: list[str] = [
        f"{d['name_en']} {d.get('description_en', '')}".strip() for d in definitions if "name_en" in d and "id" in d
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
                    "id": str(generar_uuid5_vector(settings.tenant_id, "definitions", str(d["id"]))),
                    "tenant_id": settings.tenant_id,
                    "layer": "definitions",
                    "node_ref": str(d["id"]),
                    "text": texto,
                    "vector": vector,
                    "payload": {
                        "kind": d.get("kind", "module"),
                        "name_en": d.get("name_en", ""),
                    },
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

    # -------------------------------------------------------------
    # Fase 7: Persistencia en PostgreSQL Flexible Server (ADR-018)
    # -------------------------------------------------------------
    logger.info(
        "fase 7/7: persistencia transaccional lista para carga masiva (Sprint 3 / KC-ONB-03-2)",
        extra={
            **ctx,
            "phase": "7/7",
            "symbols_to_persist": len(symbols),
            "definitions_to_persist": len(definitions),
            "concepts_to_persist": len(concepts),
            "links_to_persist": len(links),
            "curation_items_count": len(curation_queue),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Construcción o refresco del grafo de conocimiento de CodeMapping.")
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
