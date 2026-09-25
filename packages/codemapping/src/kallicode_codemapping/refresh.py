"""Módulo de refresco incremental de CodeMapping (KC-ONB-03-3 / CM-F05).

Calcula la diferencia de archivos y símbolos respecto al estado persistido
en PostgreSQL (cm.files y cm.refresh_state), purga elementos obsoletos en
una sola transacción y actualiza los embeddings correspondientes.
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

        changed, deleted = changed_paths(repo, last, head)
        logger.info(
            f"refresh: {len(changed)} cambiados, {len(deleted)} eliminados",
            extra={"tenant_id": cfg.tenant_id, "head": head},
        )

        # 1. Purgar archivos eliminados, sus símbolos, aristas y vectores asociados
        if deleted:
            ids_a_borrar = list(loader.get_symbol_ids(deleted))
            with loader.fase("purgar_eliminados") as cur:
                if ids_a_borrar:
                    cur.execute(
                        "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s AND nodo_ref = ANY(%s)",
                        (cfg.tenant_id, ids_a_borrar),
                    )
                    cur.execute(
                        "DELETE FROM cm.symbol_calls "
                        "WHERE tenant_id = %s AND (from_symbol_id = ANY(%s) OR to_symbol_id = ANY(%s))",
                        (cfg.tenant_id, ids_a_borrar, ids_a_borrar),
                    )
                    cur.execute(
                        "DELETE FROM cm.symbols WHERE tenant_id = %s AND id = ANY(%s)",
                        (cfg.tenant_id, ids_a_borrar),
                    )
                cur.execute(
                    "DELETE FROM cm.file_table_usage WHERE tenant_id = %s AND file_path = ANY(%s)",
                    (cfg.tenant_id, deleted),
                )
                cur.execute(
                    "DELETE FROM cm.files WHERE tenant_id = %s AND path = ANY(%s)",
                    (cfg.tenant_id, deleted),
                )

        # 2. Cargar archivos modificados y sus nuevos símbolos
        files = [f for p in changed if (f := load_file(repo, p))]
        symbols = [sym for f in files for sym in parse_file(f)]

        if files:
            loader.load_files(files)
        if symbols:
            loader.load_symbols(symbols)

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

        # 4. Registrar nuevo commit SHA vigente
        loader.set_last_sha(head)
        logger.info("refresh completo", extra={"head": head, "tenant_id": cfg.tenant_id})


if __name__ == "__main__":
    run_refresh()
