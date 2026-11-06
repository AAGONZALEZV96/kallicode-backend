"""
Pruebas unitarias para ScratchCleaner (KC-QA-B4-11).
Valida:
- Limpieza atómica y selectiva de scratch/{job_id}.
- Confinamiento estricto contra Path Traversal (QA-F03) con resolve_safe_path.
- Resiliencia defensiva ante directorios inexistentes o PermissionError.
- Purga recursiva de cachés huérfanas (__pycache__, .pytest_cache, .playwright-artifacts).
- Preservación rigurosa de evidencias selladas (WORM / SHA-256).
- Inmutabilidad de DTOs Pydantic v2.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.run_suite import SuiteRunner
from kallicode_testing.scratch_cleaner import ScratchCleaner, ScratchCleanSummaryDTO
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: Limpieza exitosa de scratch/{job_id}
# ---------------------------------------------------------------------------
def test_limpieza_exitosa_directorio_scratch_job(tmp_path: Path) -> None:
    """Valida la eliminación completa de un directorio scratch sintético con archivos y subdirectorios."""
    base_dir = tmp_path / "workspace"
    base_dir.mkdir()

    job_id = "job-4011-alpha"
    scratch_dir = base_dir / "scratch" / job_id
    subdir_a = scratch_dir / "sub_a"
    subdir_b = scratch_dir / "sub_b" / "nested"

    subdir_a.mkdir(parents=True)
    subdir_b.mkdir(parents=True)

    file_1 = subdir_a / "temp_1.txt"
    file_1.write_text("Hello temporary world 1", encoding="utf-8")
    file_2 = subdir_b / "temp_2.json"
    file_2.write_text('{"status": "intermediate"}', encoding="utf-8")
    file_root = scratch_dir / "root_temp.log"
    file_root.write_text("Execution log...", encoding="utf-8")

    expected_bytes = file_1.stat().st_size + file_2.stat().st_size + file_root.stat().st_size

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)
    summary = cleaner.clean_job_scratch(
        job_id=job_id,
        scratch_dir=scratch_dir,
        preserve_evidence_sealed=True,
    )

    assert isinstance(summary, ScratchCleanSummaryDTO)
    assert summary.job_id == job_id
    assert summary.files_removed_count == 3
    assert summary.bytes_freed == expected_bytes
    assert summary.duration_ms >= 0.0
    assert "T" in summary.timestamp

    # El directorio completo y sus subdirectorios deben haber sido eliminados
    assert not scratch_dir.exists()
    assert not file_1.exists()
    assert not file_2.exists()
    assert not file_root.exists()


# ---------------------------------------------------------------------------
# Prueba 2: Prevención de Path Traversal (QA-F03)
# ---------------------------------------------------------------------------
def test_prevencion_path_traversal_al_borrar(tmp_path: Path) -> None:
    """Valida que resolve_safe_path detecte intentos de path traversal y lance ValueError."""
    base_dir = tmp_path / "sandbox"
    base_dir.mkdir()

    # Archivo legítimo fuera del sandbox que NO debe ser tocado
    critical_outside_file = tmp_path / "critical_system_file.conf"
    critical_outside_file.write_text("CRITICAL_CONFIG_DO_NOT_DELETE", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)

    # 1. Intento de escape relativo hacia arriba '../..'
    traversal_path = Path("../../critical_system_file.conf")
    with pytest.raises(ValueError, match="Path-Traversal|fuera de base"):
        cleaner.clean_job_scratch(
            job_id="job-malicious",
            scratch_dir=traversal_path,
        )

    # 2. Intento de ruta absoluta fuera de base (/etc)
    with pytest.raises(ValueError, match="Path-Traversal|fuera de base"):
        cleaner.clean_job_scratch(
            job_id="job-malicious-abs",
            scratch_dir=Path("/etc"),
        )

    # El archivo crítico fuera de base debe permanecer intacto
    assert critical_outside_file.exists()
    assert critical_outside_file.read_text(encoding="utf-8") == "CRITICAL_CONFIG_DO_NOT_DELETE"


# ---------------------------------------------------------------------------
# Prueba 3: Resiliencia ante directorios inexistentes o ya eliminados
# ---------------------------------------------------------------------------
def test_resiliencia_ante_directorios_inexistentes_o_ya_eliminados(tmp_path: Path) -> None:
    """Valida manejo defensivo sin colapsos ante rutas que no existen en disco."""
    base_dir = tmp_path / "workspace"
    base_dir.mkdir()

    non_existent = base_dir / "scratch" / "ghost_job"

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)
    summary = cleaner.clean_job_scratch(
        job_id="ghost-job",
        scratch_dir=non_existent,
    )

    assert summary.job_id == "ghost-job"
    assert summary.files_removed_count == 0
    assert summary.bytes_freed == 0
    assert summary.cleaned_directories == []
    assert summary.duration_ms >= 0.0


# ---------------------------------------------------------------------------
# Prueba 4: Limpieza de cachés huérfanas (__pycache__, .pytest_cache)
# ---------------------------------------------------------------------------
def test_limpieza_caches_huerfanas_pycache_pytest(tmp_path: Path) -> None:
    """Valida la detección y purga recursiva de directorios de caché huérfana."""
    base_dir = tmp_path / "repo"
    base_dir.mkdir()

    # Estructura con varias cachés
    pkg_dir = base_dir / "packages" / "module_a"
    pkg_dir.mkdir(parents=True)
    pycache = pkg_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "module_a.cpython-312.pyc").write_bytes(b"\x00" * 128)

    pytest_cache = base_dir / ".pytest_cache" / "v" / "cache"
    pytest_cache.mkdir(parents=True)
    (pytest_cache / "lastfailed").write_text("test_something.py", encoding="utf-8")

    pw_artifacts = base_dir / "tests" / ".playwright-artifacts"
    pw_artifacts.mkdir(parents=True)
    (pw_artifacts / "trace.zip").write_bytes(b"\x50\x4b" * 32)

    # Archivo de código normal que NO debe ser borrado
    source_file = pkg_dir / "main.py"
    source_file.write_text("print('Keep me alive')", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)
    summary = cleaner.clean_orphan_caches(root_dir=base_dir)

    assert isinstance(summary, ScratchCleanSummaryDTO)
    assert summary.job_id == "orphan_caches"
    assert summary.files_removed_count == 3
    assert summary.bytes_freed > 0
    assert len(summary.cleaned_directories) >= 3

    # Las cachés deben desaparecer
    assert not pycache.exists()
    assert not (base_dir / ".pytest_cache").exists()
    assert not pw_artifacts.exists()

    # El código fuente normal debe preservarse intacto
    assert source_file.exists()
    assert source_file.read_text(encoding="utf-8") == "print('Keep me alive')"


# ---------------------------------------------------------------------------
# Prueba 5: Preservación de evidencias selladas inmutables (WORM)
# ---------------------------------------------------------------------------
def test_preservacion_evidencia_sellada_cuando_preserve_evidence_sealed_true(
    tmp_path: Path,
) -> None:
    """Valida que los artefactos sellados (.sealed, .sha256, sealed/) se preserven."""
    base_dir = tmp_path / "workspace"
    base_dir.mkdir()

    scratch_dir = base_dir / "scratch" / "job-sealed-test"
    evidence_dir = scratch_dir / "evidence_sealed"
    evidence_dir.mkdir(parents=True)

    # Evidencias selladas
    sealed_shot = evidence_dir / "screenshot_fail.png.sealed"
    sealed_shot.write_bytes(b"\x89PNG\r\n\x1a\n" * 10)
    hash_file = scratch_dir / "manifest.sha256"
    hash_file.write_text(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", encoding="utf-8"
    )

    # Archivos efímeros a eliminar
    temp_log = scratch_dir / "execution.log"
    temp_log.write_text("Debugging info...", encoding="utf-8")
    temp_dump = scratch_dir / "raw_stdout.tmp"
    temp_dump.write_text("stdout details", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)

    # Limpieza preservando evidencia sellada
    summary = cleaner.clean_job_scratch(
        job_id="job-sealed-test",
        scratch_dir=scratch_dir,
        preserve_evidence_sealed=True,
    )

    assert summary.files_removed_count == 2
    assert not temp_log.exists()
    assert not temp_dump.exists()

    # Las evidencias selladas deben seguir existiendo intactas
    assert sealed_shot.exists()
    assert hash_file.exists()
    assert scratch_dir.exists()

    # Si se desactiva preserve_evidence_sealed, todo se elimina
    summary_force = cleaner.clean_job_scratch(
        job_id="job-sealed-test",
        scratch_dir=scratch_dir,
        preserve_evidence_sealed=False,
    )

    assert summary_force.files_removed_count >= 2
    assert not scratch_dir.exists()


# ---------------------------------------------------------------------------
# Prueba 6: Manejo defensivo ante PermissionError y archivos bloqueados
# ---------------------------------------------------------------------------
def test_manejo_defensivo_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida que un PermissionError no interrumpa la ejecución ni aborte el flujo."""
    base_dir = tmp_path / "sandbox"
    base_dir.mkdir()
    scratch_dir = base_dir / "scratch" / "job-perm"
    scratch_dir.mkdir(parents=True)
    temp_file = scratch_dir / "locked.tmp"
    temp_file.write_text("locked content", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)

    # Simular PermissionError en unlink
    def mock_unlink_error(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("EACCES: Permiso denegado en sistema de archivos")

    monkeypatch.setattr(Path, "unlink", mock_unlink_error)

    # Debe capturar y continuar sin lanzar excepción
    summary = cleaner.clean_job_scratch(
        job_id="job-perm",
        scratch_dir=scratch_dir,
        preserve_evidence_sealed=True,
    )

    assert summary.files_removed_count == 0
    assert summary.bytes_freed == 0
    assert temp_file.exists()


# ---------------------------------------------------------------------------
# Prueba 7: Inmutabilidad estricta del DTO
# ---------------------------------------------------------------------------
def test_inmutabilidad_dto_scratch_clean_summary() -> None:
    """Valida que ScratchCleanSummaryDTO rechace mutaciones tras la instanciación."""
    summary = ScratchCleanSummaryDTO(
        job_id="job-immutable-1",
        cleaned_directories=["/tmp/dir1"],
        files_removed_count=5,
        bytes_freed=1024,
        duration_ms=12.5,
        timestamp="2026-11-05T23:30:00Z",
    )

    with pytest.raises(ValidationError):
        summary.files_removed_count = 10

    with pytest.raises(ValidationError):
        summary.bytes_freed = 2048


# ---------------------------------------------------------------------------
# Prueba 8: Integración con SuiteRunner (run_suite.py)
# ---------------------------------------------------------------------------
def test_integracion_suite_runner_cleanup_scratch(tmp_path: Path) -> None:
    """Valida que SuiteRunner invoque cleanup_scratch de forma desacoplada."""
    base_dir = tmp_path / "suite_ws"
    base_dir.mkdir()
    scratch_dir = base_dir / "scratch" / "job-sr"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "test_file.tmp").write_text("temporary data", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)
    runner = SuiteRunner(scratch_cleaner=cleaner)

    summary = runner.cleanup_scratch(
        job_id="job-sr",
        scratch_dir=scratch_dir,
        preserve_evidence_sealed=True,
    )

    assert summary.job_id == "job-sr"
    assert summary.files_removed_count == 1
    assert not scratch_dir.exists()


# ---------------------------------------------------------------------------
# Prueba 9: Cobertura adicional - Archivo individual y variantes de caché
# ---------------------------------------------------------------------------
def test_limpieza_archivo_individual_y_rutas_inexistentes_caches(tmp_path: Path) -> None:
    """Valida ramas cuando scratch_dir es archivo directo o raíz de caché inexistente."""
    base_dir = tmp_path / "extra_ws"
    base_dir.mkdir()

    # 1. scratch_dir apuntando a un archivo individual
    single_file = base_dir / "single_artifact.tmp"
    single_file.write_text("just one file", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)
    res_single = cleaner.clean_job_scratch("job-single", single_file)
    assert res_single.files_removed_count == 1
    assert not single_file.exists()

    # 2. scratch_dir apuntando a un archivo individual pero sellado
    sealed_single = base_dir / "single.worm"
    sealed_single.write_bytes(b"\xaa\xbb")
    res_sealed_single = cleaner.clean_job_scratch(
        "job-sealed-single", sealed_single, preserve_evidence_sealed=True
    )
    assert res_sealed_single.files_removed_count == 0
    assert sealed_single.exists()

    # 3. clean_orphan_caches con root_dir inexistente o archivo no directorio
    res_non_dir = cleaner.clean_orphan_caches(root_dir=base_dir / "non_existing_dir")
    assert res_non_dir.files_removed_count == 0
    assert res_non_dir.cleaned_directories == []

    # 4. clean_orphan_caches cuando root_dir es directamente __pycache__
    direct_cache = base_dir / "__pycache__"
    direct_cache.mkdir()
    (direct_cache / "stub.pyc").write_bytes(b"pyc")
    res_direct_cache = cleaner.clean_orphan_caches(root_dir=direct_cache)
    assert res_direct_cache.files_removed_count == 1
    assert not direct_cache.exists()


# ---------------------------------------------------------------------------
# Prueba 10: Resiliencia ante rmdir PermissionError y fallos de listado
# ---------------------------------------------------------------------------
def test_resiliencia_rmdir_permission_error_y_variantes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida manejo defensivo si rmdir o rglob lanzan PermissionError."""
    base_dir = tmp_path / "perm_ws"
    base_dir.mkdir()
    scratch_dir = base_dir / "scratch" / "job-rmdir-fail"
    subdir = scratch_dir / "sub"
    subdir.mkdir(parents=True)
    (subdir / "file.tmp").write_text("data", encoding="utf-8")

    cleaner = ScratchCleaner(allowed_base_dir=base_dir)

    def mock_rmdir_error(self: Path) -> None:
        raise PermissionError("EACCES: No se puede eliminar directorio")

    monkeypatch.setattr(Path, "rmdir", mock_rmdir_error)

    # Debe eliminar el archivo pero no colapsar al fallar rmdir
    res = cleaner.clean_job_scratch("job-rmdir-fail", scratch_dir)
    assert res.files_removed_count == 1
    assert res.cleaned_directories == []
