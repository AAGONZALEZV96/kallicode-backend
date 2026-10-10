"""Fase 1 - sincroniza el repositorio y cataloga los archivos fuente.

Determinista: git + sistema de archivos. Cada archivo lleva su hash,
que es lo que permite el refresh incremental (solo cambio lo que cambio).
Soporta resolución de ramas por tenant y fallbacks deterministas (KC-CM-04-2).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Generator, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from .config import Settings
from .logging_setup import get_logger
from .parser_treesitter import Symbol, parse_file

logger = get_logger()

LANG_BY_EXT = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".java": "java",
    ".sql": "sql",
}


@dataclass
class SourceFile:
    path: str  # relativo a la raiz del repo
    language: str
    sha256: str
    content: str


class GitSyncError(Exception):
    """Error al sincronizar el repositorio Git."""


def resolver_rama_tenant(tenant_id: str, settings: Settings, branch_map: dict[str, str] | None = None) -> str:
    """Resuelve la rama configurada para un tenant específico."""
    if branch_map and tenant_id in branch_map:
        return branch_map[tenant_id]

    target = getattr(settings, "target_branch", None)
    if isinstance(target, str) and target.strip():
        return target.strip()

    default = getattr(settings, "default_branch", None)
    if isinstance(default, str) and default.strip():
        return default.strip()

    return "main"


def verificar_ancestro_comun(repo_dir: str | Path, commit_anterior: str, commit_nuevo: str) -> bool:
    """Verifica si el commit anterior es ancestro del nuevo (detecta force-push)."""
    try:
        res = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "merge-base",
                "--is-ancestor",
                commit_anterior,
                commit_nuevo,
            ],
            capture_output=True,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def sync_repo(
    settings: Settings,
    ultimo_sha: str | None = None,
    branch_map: dict[str, str] | None = None,
) -> tuple[str, str, bool]:
    """Clona o sincroniza el repositorio Git resolviendo la rama configurada por tenant.

    Retorna: (repo_path, head_sha, requiere_full_build)
    Si se detecta force-push o divergencia de historia, retorna requiere_full_build=True.
    """
    dest = os.path.join(settings.workdir, settings.tenant_id)
    rama = resolver_rama_tenant(settings.tenant_id, settings, branch_map)
    requiere_full_build = False

    try:
        if not os.path.isdir(os.path.join(dest, ".git")):
            os.makedirs(settings.workdir, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--branch", rama, settings.repo_url, dest],
                check=True,
                capture_output=True,
                text=True,
            )
            requiere_full_build = True
        else:
            subprocess.run(
                ["git", "-C", dest, "fetch", "--prune", "origin", rama],
                check=True,
                capture_output=True,
                text=True,
            )

            nuevo_sha_res = subprocess.run(
                ["git", "-C", dest, "rev-parse", f"origin/{rama}"],
                check=True,
                capture_output=True,
                text=True,
            )
            nuevo_sha = nuevo_sha_res.stdout.strip()

            if ultimo_sha and ultimo_sha != nuevo_sha:
                es_ancestro = verificar_ancestro_comun(dest, ultimo_sha, nuevo_sha)
                if not es_ancestro:
                    logger.warning(
                        "detectado force-push o divergencia de historia para tenant; degradando a full build",
                        extra={
                            "tenant_id": settings.tenant_id,
                            "ultimo_sha": ultimo_sha,
                            "nuevo_sha": nuevo_sha,
                            "rama": rama,
                        },
                    )
                    requiere_full_build = True

            subprocess.run(
                ["git", "-C", dest, "checkout", "-B", rama, f"origin/{rama}"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", dest, "reset", "--hard", f"origin/{rama}"],
                check=True,
                capture_output=True,
                text=True,
            )

        head_res = subprocess.run(
            ["git", "-C", dest, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        head_sha = head_res.stdout.strip()

        return dest, head_sha, requiere_full_build

    except subprocess.CalledProcessError as exc:
        detalle = exc.stderr if exc.stderr else str(exc)
        logger.error(
            f"error de git en sincronizacion de tenant {settings.tenant_id}: {detalle}",
            extra={"tenant_id": settings.tenant_id, "rama": rama},
        )
        raise GitSyncError(
            f"Error sincronizando rama {rama} para tenant {settings.tenant_id}: {detalle}"
        ) from exc


def walk_sources(root: str, settings: Settings) -> Iterator[SourceFile]:
    """Itera los archivos fuente relevantes del repositorio.

    Determinista: rutas relativas POSIX (CM-F16) y orden lexicográfico por ruta.
    os.walk no garantiza orden (NTFS y ext4 devuelven órdenes distintos), y el
    orden de los archivos define el orden de símbolos y de los lotes al LLM.
    """
    candidatos: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in settings.exclude_dirs]
        for name in filenames:
            if os.path.splitext(name)[1] not in settings.include_ext:
                continue
            full = os.path.join(dirpath, name)
            candidatos.append((PurePath(os.path.relpath(full, root)).as_posix(), full))

    for posix_rel_path, full in sorted(candidatos):
        try:
            if os.path.getsize(full) > settings.max_file_bytes:
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        yield SourceFile(
            path=posix_rel_path,
            language=LANG_BY_EXT[os.path.splitext(full)[1]],
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            content=content,
        )


def dividir_en_lotes[T](
    elementos: Iterable[T], tamano_lote: int = 250
) -> Generator[list[T], None, None]:
    """Generador que divide un iterable en lotes acotados para streaming en memoria."""
    lote: list[T] = []
    for item in elementos:
        lote.append(item)
        if len(lote) >= tamano_lote:
            yield lote
            lote = []
    if lote:
        yield lote


def _parse_archivo_individual(sf: Any) -> list[Symbol]:
    """Función de nivel superior ejecutable por workers del ProcessPoolExecutor."""
    return parse_file(sf)


def parsear_fuentes_en_streaming(
    fuentes: Iterable[Any],
    tamano_lote: int = 250,
    max_workers: int | None = None,
) -> Generator[list[Symbol], None, None]:
    """Procesa archivos en streaming mediante un pool de procesos sin superar 1.5 GB RSS.

    Emite listas de Symbol por lote para permitir la persistencia incremental con COPY
    sin acumular todo el repositorio en memoria principal.
    """
    workers = max_workers or min(os.cpu_count() or 1, 4)

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for lote in dividir_en_lotes(fuentes, tamano_lote=tamano_lote):
            resultados_lote: list[Symbol] = []
            for simbolos in executor.map(_parse_archivo_individual, lote):
                resultados_lote.extend(simbolos)

            yield resultados_lote
