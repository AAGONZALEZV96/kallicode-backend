"""
Ticket: KC-QA-B4-11
Limpiador de Archivos Temporales Post-Ejecución (Scratch Cleaner).
Implementa:
- Limpieza atómica y selectiva de artefactos temporales en scratch/{job_id}.
- Confinamiento estricto mediante resolve_safe_path para prevenir Path Traversal (QA-F03).
- Respeto riguroso de evidencias selladas inmutables (WORM / SHA-256).
- Purga recursiva de cachés huérfanas (.pytest_cache, __pycache__, .playwright-artifacts).
- Resiliencia defensiva ante PermissionError y FileNotFoundError.
- DTOs inmutables Pydantic v2 (frozen=True).
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-11
# ---------------------------------------------------------------------------
class ScratchCleanSummaryDTO(BaseModel):
    """Resumen consolidado e inmutable del resultado de la limpieza de archivos temporales."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    job_id: str
    cleaned_directories: list[str]
    files_removed_count: int
    bytes_freed: int
    duration_ms: float
    timestamp: str  # ISO 8601 UTC


# ---------------------------------------------------------------------------
# Clase Principal ScratchCleaner
# ---------------------------------------------------------------------------
class ScratchCleaner:
    """
    Limpiador seguro y atómico de archivos temporales post-ejecución.
    Aplica confinamiento de rutas mediante resolve_safe_path para prevenir QA-F03,
    preserva evidencias selladas inmutables y purga cachés huérfanas de herramientas.
    """

    TARGET_CACHE_NAMES: frozenset[str] = frozenset(
        {".pytest_cache", "__pycache__", ".playwright-artifacts"}
    )

    def __init__(self, allowed_base_dir: Path | None = None) -> None:
        """
        Inicializa el limpiador estableciendo el directorio base autorizado.
        Cualquier intento de eliminación fuera de este directorio será rechazado
        con ValueError mediante resolve_safe_path.
        """
        self.allowed_base_dir = (allowed_base_dir or Path.cwd()).resolve()

    def _is_sealed_evidence(self, path: Path) -> bool:
        """
        Determina si un archivo o directorio corresponde a evidencias selladas inmutables
        (WORM / SHA-256) generadas por evidence_sealer.py que deben preservarse.
        """
        suffix = path.suffix.lower()
        if suffix in {".sealed", ".sha256", ".worm"}:
            return True

        name_lower = path.name.lower()
        if "sealed" in name_lower or "manifest" in name_lower:
            return True

        parts_lower = {p.lower() for p in path.parts}
        return bool({"sealed", "evidence_sealed", "worm"}.intersection(parts_lower))

    def clean_job_scratch(
        self,
        job_id: str,
        scratch_dir: Path,
        preserve_evidence_sealed: bool = True,
    ) -> ScratchCleanSummaryDTO:
        """
        Elimina selectiva o totalmente el directorio temporal de trabajo scratch/{job_id}.

        Garantiza:
        - Confinamiento estricto mediante resolve_safe_path (QA-F03).
        - Preservación de evidencias selladas si preserve_evidence_sealed es True.
        - Conteo de archivos eliminados y cálculo exacto de bytes liberados.
        - Resiliencia defensiva ante PermissionError o FileNotFoundError sin colapsar.
        """
        start_time = time.perf_counter()
        cleaned_directories: list[str] = []
        files_removed_count = 0
        bytes_freed = 0

        # Confinamiento estricto contra Path Traversal
        safe_scratch_dir = resolve_safe_path(self.allowed_base_dir, scratch_dir)

        # Manejo defensivo si el directorio no existe o ya fue eliminado previamente
        if not safe_scratch_dir.exists():
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ScratchCleanSummaryDTO(
                job_id=job_id,
                cleaned_directories=[],
                files_removed_count=0,
                bytes_freed=0,
                duration_ms=duration_ms,
                timestamp=datetime.now(UTC).isoformat(),
            )

        if not safe_scratch_dir.is_dir():
            # Manejo si la ruta apuntaba a un archivo individual
            try:
                if not (preserve_evidence_sealed and self._is_sealed_evidence(safe_scratch_dir)):
                    size = safe_scratch_dir.stat().st_size
                    safe_scratch_dir.unlink()
                    files_removed_count += 1
                    bytes_freed += size
            except (FileNotFoundError, PermissionError) as exc:
                logger.warning(
                    "scratch_cleaner.unlink_failed",
                    path=str(safe_scratch_dir),
                    error=str(exc),
                )

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ScratchCleanSummaryDTO(
                job_id=job_id,
                cleaned_directories=[],
                files_removed_count=files_removed_count,
                bytes_freed=bytes_freed,
                duration_ms=duration_ms,
                timestamp=datetime.now(UTC).isoformat(),
            )

        # Listar todos los elementos dentro de safe_scratch_dir
        try:
            all_items = list(safe_scratch_dir.rglob("*"))
        except (FileNotFoundError, PermissionError) as exc:
            logger.warning(
                "scratch_cleaner.list_dir_failed",
                path=str(safe_scratch_dir),
                error=str(exc),
            )
            all_items = []

        # 1. Eliminar archivos regulares o symlinks
        for item in all_items:
            try:
                if (item.is_file() or item.is_symlink()) and item.exists():
                    if preserve_evidence_sealed and self._is_sealed_evidence(item):
                        continue
                    size = item.stat().st_size
                    item.unlink()
                    files_removed_count += 1
                    bytes_freed += size
            except (FileNotFoundError, PermissionError) as exc:
                logger.warning(
                    "scratch_cleaner.file_removal_error",
                    path=str(item),
                    error=str(exc),
                )

        # 2. Eliminar subdirectorios vacíos de abajo hacia arriba (bottom-up)
        subdirs = [p for p in all_items if p.is_dir()]
        subdirs.sort(key=lambda p: len(p.parts), reverse=True)

        for subdir in subdirs:
            try:
                if subdir.exists() and not any(subdir.iterdir()):
                    subdir.rmdir()
                    cleaned_directories.append(str(subdir))
            except (FileNotFoundError, PermissionError) as exc:
                logger.warning(
                    "scratch_cleaner.dir_removal_error",
                    path=str(subdir),
                    error=str(exc),
                )

        # 3. Eliminar el directorio raíz scratch_dir si quedó vacío
        try:
            if safe_scratch_dir.exists() and not any(safe_scratch_dir.iterdir()):
                safe_scratch_dir.rmdir()
                cleaned_directories.append(str(safe_scratch_dir))
        except (FileNotFoundError, PermissionError) as exc:
            logger.warning(
                "scratch_cleaner.root_scratch_removal_error",
                path=str(safe_scratch_dir),
                error=str(exc),
            )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        logger.info(
            "scratch_cleaner.job_scratch_cleaned",
            job_id=job_id,
            scratch_dir=str(safe_scratch_dir),
            files_removed=files_removed_count,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
        )

        return ScratchCleanSummaryDTO(
            job_id=job_id,
            cleaned_directories=cleaned_directories,
            files_removed_count=files_removed_count,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def clean_orphan_caches(
        self,
        root_dir: Path,
    ) -> ScratchCleanSummaryDTO:
        """
        Escanea y purga directorios de caché huérfana (.pytest_cache, __pycache__,
        .playwright-artifacts) confinados dentro del directorio raíz autorizado.
        """
        start_time = time.perf_counter()
        cleaned_directories: list[str] = []
        files_removed_count = 0
        bytes_freed = 0

        safe_root = resolve_safe_path(self.allowed_base_dir, root_dir)

        if not safe_root.exists() or not safe_root.is_dir():
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ScratchCleanSummaryDTO(
                job_id="orphan_caches",
                cleaned_directories=[],
                files_removed_count=0,
                bytes_freed=0,
                duration_ms=duration_ms,
                timestamp=datetime.now(UTC).isoformat(),
            )

        found_cache_dirs: list[Path] = []

        # Caso especial si safe_root es directamente un directorio de caché
        if safe_root.name in self.TARGET_CACHE_NAMES:
            found_cache_dirs.append(safe_root)
        else:
            for current_root, dirs, _files in os.walk(safe_root):
                for d in list(dirs):
                    if d in self.TARGET_CACHE_NAMES:
                        cache_path = Path(current_root) / d
                        found_cache_dirs.append(cache_path)
                        # No descender más dentro de esta rama
                        dirs.remove(d)

        for cache_dir in found_cache_dirs:
            try:
                if not cache_dir.exists():
                    continue

                # Contabilizar archivos y bytes antes de eliminar
                for root_c, _dirs_c, files_c in os.walk(cache_dir):
                    for f in files_c:
                        fp = Path(root_c) / f
                        try:
                            if fp.exists():
                                bytes_freed += fp.stat().st_size
                                files_removed_count += 1
                        except (FileNotFoundError, PermissionError):
                            pass

                shutil.rmtree(cache_dir)
                cleaned_directories.append(str(cache_dir))
            except (FileNotFoundError, PermissionError) as exc:
                logger.warning(
                    "scratch_cleaner.cache_removal_error",
                    path=str(cache_dir),
                    error=str(exc),
                )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        logger.info(
            "scratch_cleaner.orphan_caches_cleaned",
            root_dir=str(safe_root),
            cleaned_dirs_count=len(cleaned_directories),
            files_removed=files_removed_count,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
        )

        return ScratchCleanSummaryDTO(
            job_id="orphan_caches",
            cleaned_directories=cleaned_directories,
            files_removed_count=files_removed_count,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            timestamp=datetime.now(UTC).isoformat(),
        )
