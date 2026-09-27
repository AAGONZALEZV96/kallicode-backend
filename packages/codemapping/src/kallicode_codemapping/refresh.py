"""Módulo de refresco incremental de CodeMapping (KC-ONB-03-3 / CM-F05).

Calcula la diferencia de archivos respecto al commit de la última
instantánea completada (cm.repo_snapshots), purga lo obsoleto -- archivos,
símbolos en cascada, aristas de cm.edges y vectores -- y reembebe lo que
cambió. Cada corrida registra su propia instantánea, igual que build_graph.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import PurePosixPath
from typing import Any

from .config import Settings, build_settings
from .embeddings import TEIEmbeddingClient, generar_uuid5_vector
from .graph_loader import GraphLoader
from .ingest_repo import LANG_BY_EXT, SourceFile, sync_repo
from .llm_enrich import describe_symbols
from .logging_setup import get_logger
from .parser_treesitter import parse_file

logger = get_logger()


def _rama_actual(repo: str) -> str:
    return (
        subprocess.run(
            ["git", "-C", repo, "branch", "--show-current"], check=True, capture_output=True, text=True
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


def run_refresh(settings: Settings | None = None) -> None:
    """Ejecuta el ciclo de refresco incremental contra PostgreSQL."""
    cfg = settings or build_settings()
    repo, head = sync_repo(cfg)

    with GraphLoader(dsn=cfg.store_database_url, tenant_id=cfg.tenant_id) as loader:
        last = loader.get_last_sha()
        if last is None:
            raise SystemExit("Sin SHA previo: ejecuta build_graph.py primero")
        if last == head:
            logger.info("Grafo al día", extra={"head": head, "tenant_id": cfg.tenant_id})
            return

        rama = _rama_actual(repo)
        snapshot_id = loader.start_snapshot(commit_sha=head, rama=rama)
        try:
            changed, deleted = changed_paths(repo, last, head)
            logger.info(
                f"refresh: {len(changed)} cambiados, {len(deleted)} eliminados",
                extra={"tenant_id": cfg.tenant_id, "head": head},
            )

            # 1. Purgar archivos eliminados: cascada a símbolos, aristas y vectores.
            if deleted:
                loader.delete_file_subgraph(deleted)

            # 2. Cargar archivos modificados y sus nuevos símbolos
            files = [f for p in changed if (f := load_file(repo, p))]
            symbols = [sym for f in files for sym in parse_file(f)]

            file_ids: dict[str, str] = {}
            if files:
                file_ids = loader.load_files(snapshot_id, files)
            if symbols:
                loader.load_symbols(symbols, file_ids)

                # 3. Enriquecimiento semántico y actualización de embeddings
                described = describe_symbols(cfg, symbols)
                by_id = {str(d["id"]): d for d in described if "id" in d}

                textos_simbolos: list[str] = [
                    f"{sym.name} {sym.signature} {by_id.get(sym.id, {}).get('description_en', '')}".strip()
                    for sym in symbols
                ]

                tei_client = TEIEmbeddingClient(base_url=cfg.tei_endpoint_url)
                vectores_simbolos = tei_client.embed_texts_sync(textos_simbolos)

                items_codigo: list[dict[str, Any]] = []
                for sym, texto, vector in zip(symbols, textos_simbolos, vectores_simbolos, strict=True):
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
                                "module": PurePosixPath(sym.file.replace("\\", "/")).parts[0]
                                if PurePosixPath(sym.file.replace("\\", "/")).parts
                                else "root",
                            },
                        }
                    )

                loader.upsert_vectors(items_codigo)

            loader.complete_snapshot(snapshot_id, estado="completo")
            logger.info("refresh completo", extra={"head": head, "tenant_id": cfg.tenant_id})
        except Exception as exc:
            loader.complete_snapshot(snapshot_id, estado="fallido", motivo=str(exc))
            raise


if __name__ == "__main__":
    run_refresh()
