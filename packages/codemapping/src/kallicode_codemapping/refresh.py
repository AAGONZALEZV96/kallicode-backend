"""Módulo de refresco incremental de CodeMapping (KC-ONB-03-3 / CM-F05).

Calcula la diferencia de archivos respecto al commit de la última
instantánea completada (cm.repo_snapshots), purga lo obsoleto -- archivos,
símbolos en cascada, aristas de cm.edges y vectores -- y reembebe lo que
cambió. Soporta fallbacks automáticos hacia full_build ante force-push (KC-CM-04-2).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import PurePosixPath
from typing import Any

from .build_graph import descripciones_de_simbolos, full_build
from .config import Settings, build_settings
from .embeddings import TEIEmbeddingClient, generar_uuid5_vector, texto_embedding_simbolo
from .fase_documental import refrescar_capa_documental
from .graph_loader import GraphLoader
from .ingest_repo import LANG_BY_EXT, SourceFile, sync_repo
from .llm_enrich import describe_symbols
from .logging_setup import get_logger
from .metricas import registrar_refresco_reachability
from .parser_treesitter import parse_file
from .vigencia import ResultadoVigencia, reparar_vigencia

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


def changed_paths(repo: str, since_sha: str, head_sha: str) -> tuple[list[str], list[str]]:
    """Obtiene los archivos modificados y eliminados vía git diff."""
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--name-status", since_sha, head_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    changed: list[str] = []
    deleted: list[str] = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if os.path.splitext(path)[1] not in LANG_BY_EXT:
            continue
        (deleted if status.startswith("D") else changed).append(path)
    return changed, deleted


def load_file(repo: str, path: str) -> SourceFile | None:
    """Carga y calcula el hash de un archivo fuente."""
    full = os.path.join(repo, path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None
    return SourceFile(
        path=path,
        language=LANG_BY_EXT[os.path.splitext(path)[1]],
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content=content,
    )


def reparar_vigencia_del_refresco(loader: GraphLoader, cfg: Settings) -> ResultadoVigencia | None:
    """Paso de vigencia del refresco. No es fatal: el grafo ya está cargado y lo que no se repare
    ahora sale como obsoleto en la métrica y se repara en la corrida siguiente."""
    try:
        return reparar_vigencia(
            loader,
            TEIEmbeddingClient(base_url=cfg.tei_endpoint_url),
            cfg.vigencia_max_reembebidos,
            tamano_lote=cfg.tei_batch_size,
        )
    except Exception:
        logger.warning(
            "no se pudo reparar la vigencia de los embeddings; se reintenta en el próximo refresco",
            extra={"tenant_id": cfg.tenant_id},
            exc_info=True,
        )
        return None


def refrescar_reachability_del_refresco(loader: GraphLoader, cfg: Settings) -> str | None:
    """Refresca el cierre transitivo materializado (cm.reachability) tras cambios en cm.edges.

    No es fatal: si supera el presupuesto o falla, cm.impacto() cae automáticamente al CTE
    recursivo en vivo y lo registra en la métrica (KC-CM-11-2).
    """
    inicio = time.perf_counter()
    try:
        with loader.fase("reachability") as cur:
            cur.execute("SELECT estado, filas, caminos FROM cm.refrescar_reachability()")
            fila = cur.fetchone()
        estado = str(fila[0]) if fila else "error"
        registrar_refresco_reachability(estado, time.perf_counter() - inicio)
        logger.info(
            "reachability refrescada",
            extra={
                "tenant_id": cfg.tenant_id,
                "estado": estado,
                "filas": int(fila[1]) if fila else 0,
                "caminos": int(fila[2]) if fila else 0,
            },
        )
        return estado
    except Exception:
        registrar_refresco_reachability("error", time.perf_counter() - inicio)
        logger.warning(
            "no se pudo refrescar cm.reachability; las consultas usarán recorrido recursivo",
            extra={"tenant_id": cfg.tenant_id},
            exc_info=True,
        )
        return None


def run_refresh(settings: Settings | None = None) -> None:
    """Ejecuta el ciclo de refresco incremental contra PostgreSQL."""
    cfg = settings or build_settings()

    # 1. Obtener primero el último SHA confirmado desde la base de datos
    with GraphLoader(dsn=cfg.store_database_url, tenant_id=cfg.tenant_id) as loader:
        last = loader.get_last_sha()

    if last is None:
        logger.info("Sin SHA previo registrado: ejecutando full build inicial")
        full_build(cfg)
        return

    # 2. Sincronizar el repositorio evaluando divergencia respecto a last
    repo, head, requiere_full = sync_repo(cfg, ultimo_sha=last)
    if requiere_full:
        logger.info("ejecutando full build por divergencia de historia (KC-CM-04-2)")
        full_build(cfg)
        return

    if last == head:
        logger.info("Grafo al día", extra={"head": head, "tenant_id": cfg.tenant_id})
        # El código no cambió, pero la documentación puede haber derivado (KC-CM-08-5): las stats
        # quedan en la última instantánea completa.
        with GraphLoader(dsn=cfg.store_database_url, tenant_id=cfg.tenant_id) as loader:
            refrescar_capa_documental(loader, cfg, loader.obtener_ultimo_snapshot_completo())
            # Sin commits nuevos una descripción curada o un cambio de modelo también dejan
            # embeddings obsoletos (KC-CM-11-4).
            reparar_vigencia_del_refresco(loader, cfg)
            # KC-CM-11-2: refrescar cm.reachability si quedó obsoleta por curación o realimentación.
            refrescar_reachability_del_refresco(loader, cfg)
        return

    # 3. Proceder con el refresco incremental si la historia es lineal
    with GraphLoader(dsn=cfg.store_database_url, tenant_id=cfg.tenant_id) as loader:
        rama = _rama_actual(repo)
        snapshot_id = loader.start_snapshot(commit_sha=head, rama=rama)
        try:
            changed, deleted = changed_paths(repo, last, head)
            logger.info(
                f"refresh: {len(changed)} cambiados, {len(deleted)} eliminados",
                extra={"tenant_id": cfg.tenant_id, "head": head},
            )

            # Purgar archivos eliminados: cascada a símbolos, aristas y vectores
            if deleted:
                loader.delete_file_subgraph(deleted)

            # Cargar archivos modificados y sus nuevos símbolos
            files = [f for p in changed if (f := load_file(repo, p))]
            symbols = [sym for f in files for sym in parse_file(f)]

            file_ids: dict[str, str] = {}
            if files:
                file_ids = loader.load_files(snapshot_id, files)
            if symbols:
                loader.load_symbols(symbols, file_ids)

                described = describe_symbols(cfg, symbols)
                by_id = {str(d["id"]): d for d in described if "id" in d}
                # KC-CM-11-4: la descripción se guarda en cm.symbols (vigencia en base).
                descripciones = descripciones_de_simbolos(symbols, by_id)
                loader.actualizar_descripciones_simbolos(descripciones)

                textos_simbolos: list[str] = [
                    texto_embedding_simbolo(sym.name, sym.signature, descripciones.get(sym.id))
                    for sym in symbols
                ]

                tei_client = TEIEmbeddingClient(base_url=cfg.tei_endpoint_url)
                vectores_simbolos = tei_client.embed_texts_sync(textos_simbolos)

                items_codigo: list[dict[str, Any]] = []
                for sym, texto, vector in zip(
                    symbols, textos_simbolos, vectores_simbolos, strict=True
                ):
                    items_codigo.append(
                        {
                            "id": str(generar_uuid5_vector(cfg.tenant_id, "code", sym.id)),
                            "tenant_id": cfg.tenant_id,
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

                loader.upsert_vectors(items_codigo)

            # Capa documental (KC-CM-08-5/08-6): purga, reutilización por hash y presupuesto. Si se
            # pasa del presupuesto o falla, solo se detiene esa fase; el código ya está cargado.
            refrescar_capa_documental(loader, cfg, snapshot_id)

            # KC-CM-11-4: reembebe lo que quedó obsoleto (también fuera de los ficheros cambiados).
            reparar_vigencia_del_refresco(loader, cfg)

            # KC-CM-11-2: refresca el cierre transitivo materializado tras actualizar cm.edges.
            refrescar_reachability_del_refresco(loader, cfg)

            loader.complete_snapshot(snapshot_id, estado="completo")
            logger.info("refresh completo", extra={"head": head, "tenant_id": cfg.tenant_id})
        except Exception as exc:
            loader.complete_snapshot(snapshot_id, estado="fallido", motivo=str(exc))
            raise


if __name__ == "__main__":
    run_refresh()
