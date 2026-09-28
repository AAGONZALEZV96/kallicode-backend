"""Fase 1 - sincroniza el repositorio y cataloga los archivos fuente.

Determinista: git + sistema de archivos. Cada archivo lleva su hash,
que es lo que permite el refresh incremental (solo cambio lo que cambio).
"""

import hashlib
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePath

from .config import Settings

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


def sync_repo(settings: Settings) -> tuple[str, str]:
    """Clona (o actualiza) el repo. Devuelve (directorio, sha de HEAD)."""
    dest = os.path.join(settings.workdir, settings.tenant_id)
    if os.path.isdir(os.path.join(dest, ".git")):
        subprocess.run(["git", "-C", dest, "fetch", "--prune"], check=True)
        subprocess.run(["git", "-C", dest, "reset", "--hard", "origin/HEAD"], check=True)
    else:
        os.makedirs(settings.workdir, exist_ok=True)
        subprocess.run(["git", "clone", settings.repo_url, dest], check=True)
    head = subprocess.run(
        ["git", "-C", dest, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return dest, head.stdout.strip()


def walk_sources(root: str, settings: Settings) -> Iterator[SourceFile]:
    """Itera los archivos fuente relevantes del repositorio."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in settings.exclude_dirs]
        for name in filenames:
            ext = os.path.splitext(name)[1]
            if ext not in settings.include_ext:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > settings.max_file_bytes:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue

            # Normalización a rutas POSIX (CM-F16)
            rel_path = os.path.relpath(full, root)
            posix_rel_path = PurePath(rel_path).as_posix()

            yield SourceFile(
                path=posix_rel_path,
                language=LANG_BY_EXT[ext],
                sha256=hashlib.sha256(content.encode()).hexdigest(),
                content=content,
            )
