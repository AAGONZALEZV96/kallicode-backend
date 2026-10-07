"""Fase 1 - sincroniza el repositorio y cataloga los archivos fuente.

Determinista: git + sistema de archivos. Cada archivo lleva su hash,
que es lo que permite el refresh incremental (solo cambio lo que cambio).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Generator, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from .config import Settings
from .parser_treesitter import Symbol, parse_file

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
            # Normalización a rutas POSIX (CM-F16)
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
            # Mapear el parseo del lote a través del pool de procesos
            resultados_lote: list[Symbol] = []
            for simbolos in executor.map(_parse_archivo_individual, lote):
                resultados_lote.extend(simbolos)

            yield resultados_lote
