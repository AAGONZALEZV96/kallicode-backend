"""Orquestador del pipeline de construcción del grafo de conocimiento CodeMapping.

Uso:
    uv run python -m kallicode_codemapping.build_graph --full
    uv run python -m kallicode_codemapping.build_graph --refresh

Cubre las 7 fases del pipeline según ADR-018:
    1. Sincronización determinista del repositorio Git (KC-CM-04-2)
    2. Extracción sintáctica AST con tree-sitter en streaming (KC-CM-04-1)
    3. Introspección relacional y mapeo de tablas
    4. Enriquecimiento semántico con LLM (descripción, consolidación y ontología)
    5. Deduplicación y enlace semántico
    6. Vectorización densa (BGE-M3 vía TEI) con UUIDv5 deterministas (KC-ONB-02-2)
    7. Persistencia relacional en PostgreSQL 16 con telemetría estructurada (KC-CM-04-4)
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

from kallicode_codemapping.snapshots import SnapshotManager

from . import checkpoints
from .budget import BudgetExceededError, verificar_presupuesto_tenant
from .config import Settings, build_settings
from .curation import fabricar_curation_items
from .embeddings import (
    TEIEmbeddingClient,
    embeber_en_lotes,
    generar_uuid5_vector,
    texto_embedding_definicion,
    texto_embedding_simbolo,
)
from .extract_sql import detectar_uso_tablas, extract_schema, stats_uso_tablas
from .fase_documental import construir_control_docs, registrar_resultado_documental
from .graph_loader import GraphLoader
from .ingest_docs import LectorBlobHttp, procesar_documentos_pendientes, reenlazar_documentos
from .ingest_repo import SourceFile, parsear_fuentes_en_streaming, sync_repo, walk_sources
from .llm_enrich import (
    ConsumoLLM,
    consolidate_definitions,
    contabilizar_llm,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from .logging_setup import get_logger
from .nuggets import proponer_nuggets
from .parser_treesitter import Symbol
from .redaccion import mensaje_de_error

logger = get_logger()


def descripciones_de_simbolos(
    symbols: list[Symbol], described_by_id: dict[str, dict[str, Any]]
) -> dict[str, str]:
    """Descripción LLM (description_en) de cada símbolo descrito, por id (KC-CM-11-4)."""
    return {
        sym.id: str(described_by_id[sym.id]["description_en"])
        for sym in symbols
        if described_by_id.get(sym.id, {}).get("description_en")
    }


def _registrar_consumo_llm(
    loader: GraphLoader, snapshot_id: str, consumo: ConsumoLLM, ctx: dict[str, Any], fase: str
) -> None:
    """Vuelca a core.usage_tokens (origen 'codemapping_codigo') el consumo LLM de un tramo."""
    filas = loader.registrar_consumo_codigo(snapshot_id, consumo.filas())
    logger.info(
        f"consumo LLM registrado: {consumo.tokens} tokens en {filas} filas",
        extra={**ctx, "phase": fase, "tokens_llm": consumo.tokens},
    )


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


ALCANCES_BUILD = ("completo", "determinista")

# Checkpoints locales de la salida del LLM (KC-CM-11-5), además del de la ingesta (fases 1-3).
FASE_SEMANTICA_LLM = "semantica_llm"
FASE_ONTOLOGIA = "ontologia"


def full_build(settings: Settings, alcance: str = "completo") -> None:
    """Ejecuta el pipeline completo de ingesta, enriquecimiento y vectorización.

    `alcance="determinista"` (KC-CM-12-1, recuperación ante desastre) reconstruye solo la capa que sale del
    repositorio y de la base del cliente: archivos, símbolos, tablas, columnas y sus aristas, más el cierre
    transitivo. No llama al LLM ni a TEI y no toca definiciones, conceptos, cola de curación ni vectores: son la
    capa semántica y humana, que se restaura desde la copia de seguridad (core.politica_recuperacion).

    Cada corrida queda registrada en cm.repo_snapshots. Si muere a mitad de
    camino, el siguiente intento sobre el mismo commit reanuda la misma
    instantánea (KC-ONB-04-4): las fases 1-3 (ingesta determinista) se
    recuperan de un checkpoint en disco en lugar de recalcularse. Desde
    KC-CM-11-5 también la salida del LLM (descripciones, definiciones,
    ontología y enlaces): una caída posterior, p. ej. de la base, no vuelve a
    pagar el LLM ni a reservar presupuesto. Los embeddings (TEI, autoalojado y
    sin coste por token) sí se recalculan.
    """
    if alcance not in ALCANCES_BUILD:
        raise ValueError(f"alcance {alcance!r} no válido: {' | '.join(ALCANCES_BUILD)}")
    trace_id = str(uuid.uuid4())
    repo, head, _ = sync_repo(settings)
    rama = _rama_actual(repo)

    ctx: dict[str, Any] = {
        "trace_id": trace_id,
        "tenant_id": settings.tenant_id,
        "head": head,
        "branch": rama,
    }

    snap_mgr = SnapshotManager(dsn=settings.store_database_url, tenant_id=settings.tenant_id)
    snapshot_id, _estado, _ = snap_mgr.obtener_o_crear_snapshot(
        commit_sha=head,
        rama=rama,
        trace_id=trace_id,
    )

    with GraphLoader(dsn=settings.store_database_url, tenant_id=settings.tenant_id) as loader:
        try:
            _full_build_con_snapshot(
                settings=settings,
                loader=loader,
                snap_mgr=snap_mgr,
                snapshot_id=snapshot_id,
                repo=repo,
                head=head,
                rama=rama,
                ctx=ctx,
                alcance=alcance,
            )
        except Exception as exc:
            motivo = mensaje_de_error(exc)
            snap_mgr.marcar_fallido(snapshot_id, motivo, fase=0, trace_id=trace_id)
            loader.complete_snapshot(snapshot_id, estado="fallido", motivo=motivo)
            raise


def _cerrar_reconstruccion_determinista(
    settings: Settings,
    loader: GraphLoader,
    snap_mgr: SnapshotManager,
    snapshot_id: str,
    head: str,
    ctx: dict[str, Any],
    n_archivos: int,
    n_simbolos: int,
    n_tablas: int,
) -> None:
    """Cierra una reconstrucción determinista: recalcula la alcanzabilidad y completa el snapshot."""
    try:
        with loader.fase("reachability") as cur:
            cur.execute("SELECT estado, filas, caminos FROM cm.refrescar_reachability()")
            estado_reach = cur.fetchone()
    except Exception:
        # Es derivada: si falla, las consultas caen al recorrido recursivo y el refresco diario la repone.
        logger.warning(
            "no se pudo recalcular cm.reachability tras la reconstrucción",
            extra=ctx,
            exc_info=True,
        )
        estado_reach = None
    loader.marcar_fase(snapshot_id, "completo")
    loader.complete_snapshot(snapshot_id, estado="completo")
    snap_mgr.marcar_completado(
        snapshot_id,
        total_stats={
            "files_persisted": n_archivos,
            "symbols_persisted": n_simbolos,
        },
        trace_id=ctx["trace_id"],
    )
    checkpoints.limpiar(settings.workdir, settings.tenant_id, snapshot_id)
    logger.info(
        f"reconstrucción determinista completa @ {head[:10]}",
        extra={
            **ctx,
            "phase": "reconstruccion",
            "files_persisted": n_archivos,
            "symbols_persisted": n_simbolos,
            "reachability": estado_reach[0] if estado_reach else "no_calculada",
        },
    )


def _full_build_con_snapshot(
    settings: Settings,
    loader: GraphLoader,
    snap_mgr: SnapshotManager,
    snapshot_id: str,
    repo: str,
    head: str,
    rama: str,
    ctx: dict[str, Any],
    alcance: str = "completo",
) -> None:
    trace_id: str = ctx["trace_id"]

    # -------------------------------------------------------------
    # Recuperación de Checkpoint previo si existe
    # -------------------------------------------------------------
    checkpoint_fase = snap_mgr.obtener_checkpoint(snapshot_id)
    if checkpoint_fase > 0:
        logger.info(
            f"reanudando pipeline desde checkpoint fase {checkpoint_fase}",
            extra={**ctx, "phase": f"resume-{checkpoint_fase}"},
        )

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
        # Checkpoints anteriores a KC-CM-09-3 no traen la cobertura: solo el número de aristas.
        uses_table: dict[str, Any] = cache.get("uses_table") or stats_uso_tablas(usage)
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

        # -------------------------------------------------------------
        # Fase 2: Parseo AST en streaming con pool de procesos (KC-CM-04-1)
        # -------------------------------------------------------------
        symbols = []
        for lote_simbolos in parsear_fuentes_en_streaming(files, tamano_lote=250):
            symbols.extend(lote_simbolos)

        logger.info(
            f"parseados {len(symbols)} simbolos en streaming con pool de procesos",
            extra={**ctx, "phase": "2/7", "symbol_count": len(symbols)},
        )

        tables, fks, usage = [], [], []
        uses_table = stats_uso_tablas([])
        if settings.database_url:
            tables, fks = extract_schema(settings)
            # SQL crudo + ORM (Prisma, TypeORM, SQLAlchemy, JPA), solo tablas del esquema
            uso_tablas = detectar_uso_tablas(files, tables)
            usage = uso_tablas.usage
            uses_table = uso_tablas.stats()
            logger.info(
                f"esquema: {len(tables)} tablas, {len(fks)} fks, {len(usage)} usos",
                extra={
                    **ctx,
                    "phase": "3/7",
                    "table_count": len(tables),
                    "usage_count": len(usage),
                    "uses_table_cobertura": uses_table["cobertura"],
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
                "uses_table": uses_table,
            },
        )

    file_ids = loader.load_files(snapshot_id, files)
    loader.load_symbols(symbols, file_ids)
    if tables:
        loader.load_schema(tables, fks, usage, file_ids)
    loader.marcar_fase(snapshot_id, "ingesta")
    snap_mgr.registrar_checkpoint(
        snapshot_id,
        fase=3,
        stats_fase={
            "files_count": len(files),
            "symbols_count": len(symbols),
            "tables_count": len(tables),
            "uses_table": uses_table,
        },
        trace_id=trace_id,
    )

    if alcance == "determinista":
        _cerrar_reconstruccion_determinista(
            settings,
            loader,
            snap_mgr,
            snapshot_id,
            head,
            ctx,
            len(files),
            len(symbols),
            len(tables),
        )
        return

    # -------------------------------------------------------------
    # Fases 4-5 con checkpoint (KC-CM-11-5): si la corrida anterior ya pagó el LLM y murió
    # después (p. ej. una caída de la base), se reutiliza su salida en lugar de volver a
    # llamarlo; tampoco se repite la estimación en seco, porque no se va a gastar nada.
    # -------------------------------------------------------------
    cache_llm = checkpoints.cargar(
        settings.workdir, settings.tenant_id, snapshot_id, FASE_SEMANTICA_LLM
    )
    if cache_llm is not None:
        described: list[dict[str, Any]] = cache_llm["described"]
        definitions: list[dict[str, Any]] = cache_llm["definitions"]
        logger.info(
            "fases 4-5: descripciones y definiciones reanudadas desde checkpoint local",
            extra={
                **ctx,
                "phase": "4/7 (cache)",
                "definition_count": len(definitions),
            },
        )
    else:
        # -------------------------------------------------------------
        # Estimación en seco y verificación de presupuesto (KC-CM-05-5 / CM-F07)
        # -------------------------------------------------------------
        logger.info(
            "ejecutando estimacion en seco de tokens previa al enriquecimiento",
            extra={**ctx, "phase": "dry-run", "symbol_count": len(symbols)},
        )
        try:
            # Usamos la conexión viva del propio loader para consultar core.usage_tokens
            presupuesto = verificar_presupuesto_tenant(
                conn=loader.conn,
                tenant_id=settings.tenant_id,
                simbolos=[dataclasses.asdict(s) for s in symbols],
                cuota_tokens=settings.presupuesto_tokens_tenant_mes,
            )
            logger.info(
                f"presupuesto validado: {presupuesto.total_tokens_estimados} tokens estimados "
                f"(~${presupuesto.costo_usd_estimado:.4f} USD) | Disponible: {presupuesto.cuota_disponible} tokens",
                extra={
                    **ctx,
                    "phase": "dry-run",
                    "tokens_estimados": presupuesto.total_tokens_estimados,
                    "cuota_disponible": presupuesto.cuota_disponible,
                },
            )
        except BudgetExceededError as err:
            motivo = mensaje_de_error(err)
            logger.error(
                "abortando build por presupuesto insuficiente",
                extra={**ctx, "phase": "dry-run-abort", "error": motivo},
            )
            snap_mgr.marcar_fallido(snapshot_id, motivo, fase=3, trace_id=trace_id)
            loader.complete_snapshot(snapshot_id, estado="fallido", motivo=motivo)
            raise

        # -------------------------------------------------------------
        # Fases 4-5: Enriquecimiento semántico con LLM
        # -------------------------------------------------------------
        with contabilizar_llm() as consumo_fase4:
            described = describe_symbols(
                symbols, batch_size=settings.batch_size, tenant_id=settings.tenant_id
            )
            por_id: dict[str, dict[str, Any]] = {str(d["id"]): d for d in described if "id" in d}

            by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for sym in symbols:
                desc_item = por_id.get(sym.id)
                if desc_item:
                    posix_path = PurePosixPath(sym.file.replace("\\", "/"))
                    modulo = posix_path.parts[0] if posix_path.parts else "root"
                    by_module[modulo].append(desc_item)

            definitions = [
                d
                for mod, syms in by_module.items()
                for d in consolidate_definitions(mod, syms, tenant_id=settings.tenant_id)
            ]
        # El consumo se registra antes del checkpoint: si la corrida cae entre los dos, se
        # contabiliza de más un gasto real en lugar de perderlo.
        _registrar_consumo_llm(loader, snapshot_id, consumo_fase4, ctx, "4/7")
        logger.info(
            f"definiciones consolidadas: {len(definitions)}",
            extra={**ctx, "phase": "4/7", "definition_count": len(definitions)},
        )
        checkpoints.guardar(
            settings.workdir,
            settings.tenant_id,
            snapshot_id,
            FASE_SEMANTICA_LLM,
            {"described": described, "definitions": definitions},
        )
    described_by_id: dict[str, dict[str, Any]] = {str(d["id"]): d for d in described if "id" in d}

    # -------------------------------------------------------------
    # Fase 5: Enlaces ontológicos y derivación a cola de curación
    # -------------------------------------------------------------

    # -------------------------------------------------------------
    # Fase 5: Inducción de Ontología y Enlace a Definiciones (KC-CM-07-1 / KC-CM-06-5)
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # Paso 5/5 de onboarding: ingesta de documentación (KC-CM-08-1 / CM-F22)
    # -------------------------------------------------------------
    tei_client = TEIEmbeddingClient(base_url=settings.tei_endpoint_url)
    # KC-CM-08-6: estimación en seco y presupuesto propios de la fase documental. Si se supera,
    # solo se detiene esta fase (los documentos sin procesar quedan pendientes).
    control_docs = construir_control_docs(loader, settings)
    resultados_docs = procesar_documentos_pendientes(
        loader,
        LectorBlobHttp(settings.blob_account_url, settings.blob_container),
        tei_client,
        settings.tenant_id,
        control=control_docs,
        tamano_max_caracteres=settings.docs_chunk_max_chars,
        solape_caracteres=settings.docs_chunk_overlap_chars,
        umbral_semantico=settings.docs_link_threshold,
        top_k_semantico=settings.docs_link_top_k,
        tamano_lote_tei=settings.tei_batch_size,
    )
    registrar_resultado_documental(loader, snapshot_id, control_docs, resultados=resultados_docs)
    docs_excerpts = loader.obtener_extractos_docs(limite=settings.docs_ontology_max_excerpts)

    # Propuesta de nuggets (KC-CM-08-2): solo de los documentos procesados en esta corrida. Quedan
    # en 'propuesto' con su revisión pendiente; ninguno se aprueba sin una persona (041).
    nuggets_nuevos = 0
    docs_procesados = [r.documento_id for r in resultados_docs if r.estado == "procesado"]
    if settings.nuggets_enabled and docs_procesados:
        _, nuggets_nuevos = proponer_nuggets(
            loader,
            settings.tenant_id,
            documento_ids=docs_procesados,
            max_chunks_por_lote=settings.nuggets_max_chunks_por_lote,
            max_caracteres_por_lote=settings.nuggets_max_caracteres_por_lote,
        )
    logger.info(
        f"documentacion: {len(resultados_docs)} documentos ingeridos, "
        f"{len(docs_excerpts)} extractos para la ontologia",
        extra={
            **ctx,
            "phase": "5/7-docs",
            "docs_procesados": sum(r.estado == "procesado" for r in resultados_docs),
            "docs_fallidos": sum(r.estado == "fallido" for r in resultados_docs),
            "docs_excerpts_count": len(docs_excerpts),
        },
    )

    cache_onto = checkpoints.cargar(
        settings.workdir, settings.tenant_id, snapshot_id, FASE_ONTOLOGIA
    )
    if cache_onto is not None:
        ontology: dict[str, Any] = cache_onto["ontology"]
        links: list[dict[str, Any]] = cache_onto["links"]
        raw_curation_queue: list[dict[str, Any]] = cache_onto["raw_curation_queue"]
        logger.info(
            "fase 5: ontologia y enlaces reanudados desde checkpoint local",
            extra={**ctx, "phase": "5/7 (cache)", "links_count": len(links)},
        )
    else:
        with contabilizar_llm() as consumo_fase5:
            ontology = induce_ontology(
                definitions, docs_excerpts=docs_excerpts, tenant_id=settings.tenant_id
            )
            links, raw_curation_queue = link_definitions(
                definitions=definitions,
                concepts=ontology.get("concepts", []),
                batch_size=settings.batch_size,
                tenant_id=settings.tenant_id,
                conn=loader.conn,
            )
        _registrar_consumo_llm(loader, snapshot_id, consumo_fase5, ctx, "5/7")
        checkpoints.guardar(
            settings.workdir,
            settings.tenant_id,
            snapshot_id,
            FASE_ONTOLOGIA,
            {"ontology": ontology, "links": links, "raw_curation_queue": raw_curation_queue},
        )
    concepts = ontology.get("concepts", [])

    # -------------------------------------------------------------
    # Persistencia de Cola de Curación (KC-CM-07-2)
    # -------------------------------------------------------------
    curation_items = fabricar_curation_items(
        raw_links=raw_curation_queue,
        tenant_id=settings.tenant_id,
        snapshot_id=snapshot_id,
    )
    total_curacion = loader.load_curation_queue(snapshot_id, curation_items)
    logger.info(
        f"encolados {total_curacion} elementos en cm.curation_queue",
        extra={
            **ctx,
            "phase": "5/7",
            "curation_items_count": total_curacion,
            "accepted_links_count": len(links),
        },
    )
    loader.marcar_fase(snapshot_id, "semantica")
    snap_mgr.registrar_checkpoint(
        snapshot_id,
        fase=5,
        stats_fase={
            "definitions_count": len(definitions),
            "concepts_count": len(concepts),
            "links_count": len(links),
        },
        trace_id=trace_id,
    )

    # -------------------------------------------------------------
    # Fase 6: Generación de embeddings vía TEI con UUIDv5 (KC-ONB-02-2)
    # -------------------------------------------------------------

    # KC-CM-11-4: la descripción LLM forma parte del texto embebido; se guarda en cm.symbols para que
    # la base pueda comprobar la vigencia de cada embedding (vec.vigencia_embeddings).
    descripciones = descripciones_de_simbolos(symbols, described_by_id)
    loader.actualizar_descripciones_simbolos(descripciones)
    textos_simbolos: list[str] = [
        texto_embedding_simbolo(sym.name, sym.signature, descripciones.get(sym.id))
        for sym in symbols
    ]
    items_codigo: list[dict[str, Any]] = []
    if textos_simbolos:
        logger.info(
            f"generando embeddings TEI para {len(textos_simbolos)} simbolos",
            extra={**ctx, "phase": "6/7", "count": len(textos_simbolos)},
        )
        vectores_simbolos = embeber_en_lotes(
            tei_client, textos_simbolos, tamano_lote=settings.tei_batch_size
        )
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
        texto_embedding_definicion(d["name_en"], d.get("description_en", ""))
        for d in definitions
        if "name_en" in d and "id" in d
    ]
    items_defs: list[dict[str, Any]] = []
    if textos_defs:
        logger.info(
            f"generando embeddings TEI para {len(textos_defs)} definiciones",
            extra={**ctx, "phase": "6/7", "count": len(textos_defs)},
        )
        vectores_defs = embeber_en_lotes(
            tei_client, textos_defs, tamano_lote=settings.tei_batch_size
        )
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

    loader.marcar_fase(snapshot_id, "vectores")
    snap_mgr.registrar_checkpoint(
        snapshot_id,
        fase=6,
        stats_fase={
            "code_vectors_count": len(items_codigo),
            "def_vectors_count": len(items_defs),
        },
        trace_id=trace_id,
    )

    # -------------------------------------------------------------
    # Fase 7: Persistencia semántica y vectorial (ADR-018)
    # -------------------------------------------------------------
    loader.load_semantic(definitions, ontology, links)
    loader.upsert_vectors(items_codigo + items_defs)
    # Con definiciones, conceptos y vectores ya persistidos, los chunks de documentación se
    # enlazan también a los nodos de este snapshot (no solo a los que existían al ingerirlos).
    enlaces_docs = reenlazar_documentos(
        loader,
        umbral_semantico=settings.docs_link_threshold,
        top_k_semantico=settings.docs_link_top_k,
    )
    loader.marcar_fase(snapshot_id, "completo")

    loader.complete_snapshot(snapshot_id, estado="completo")
    snap_mgr.marcar_completado(
        snapshot_id,
        total_stats={
            "files_persisted": len(files),
            "symbols_persisted": len(symbols),
            "definitions_persisted": len(definitions),
            "concepts_persisted": len(concepts),
            "links_persisted": len(links),
            "vectors_persisted": len(items_codigo) + len(items_defs),
            "curation_items_count": len(raw_curation_queue),
        },
        trace_id=trace_id,
    )
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
            "curation_items_count": len(raw_curation_queue),
            # Fuera de cm.repo_snapshots.stats: el contrato ingesta-stats-v1 está congelado.
            "docs_processed": sum(r.estado == "procesado" for r in resultados_docs),
            "docs_failed": sum(r.estado == "fallido" for r in resultados_docs),
            "doc_links_persisted": enlaces_docs,
            "nuggets_proposed": nuggets_nuevos,
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
    parser.add_argument(
        "--alcance",
        choices=ALCANCES_BUILD,
        default="completo",
        help="Con --full: 'determinista' reconstruye solo la capa determinista (sin LLM ni TEI; recuperación "
        "ante desastre, docs/runbook_reconstruccion_grafo.md); 'completo' (por defecto) hace la ingesta entera.",
    )
    args = parser.parse_args()

    if args.refresh:
        from .refresh import run_refresh

        run_refresh()
    elif args.full:
        app_settings = build_settings()
        full_build(app_settings, alcance=args.alcance)
    else:
        parser.print_help()
        sys.exit(1)
