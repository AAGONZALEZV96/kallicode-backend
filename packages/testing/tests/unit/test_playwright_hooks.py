"""
Ticket: KC-QA-B4-08
Suite de pruebas unitarias y de resiliencia para PlaywrightHookManager (playwright_hooks.py).
Valida:
1. test_captura_evidencias_ante_fallo_e2e: Captura de PNG, WEBM y ZIP ante fallo.
2. test_politica_only_on_failure_purga_exitosos: Purga automática de artefactos en ejecuciones exitosas.
3. test_prevencion_path_traversal_en_rutas_de_evidencia: Bloqueo de path traversal con resolve_safe_path (QA-F03).
4. test_generacion_configuracion_playwright_valida: Verificación de diccionario de configuración.
5. test_inmutabilidad_dtos_y_casos_borde: DTOs frozen y modos condicionales 'off'.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kallicode_testing.playwright_hooks import (
    CapturedEvidenceItemDTO,
    PlaywrightHookConfig,
    PlaywrightHookManager,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: test_captura_evidencias_ante_fallo_e2e
# ---------------------------------------------------------------------------
def test_captura_evidencias_ante_fallo_e2e(tmp_path: Path) -> None:
    """
    Prueba 1: Simular una prueba E2E fallida.
    Invocar on_test_failure y verificar que se capturen:
    - Captura de pantalla PNG
    - Video WEBM
    - Traza ZIP
    Todos asociados a AC-01 y job-e2e-100.
    """
    manager = PlaywrightHookManager()
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Configurar mock de Page y Context de Playwright
    mock_page = MagicMock()
    mock_tracing = MagicMock()
    mock_context = MagicMock()
    mock_context.tracing = mock_tracing
    mock_page.context = mock_context

    # Simular objeto de video con archivo existente
    dummy_video_file = tmp_path / "raw_video.webm"
    dummy_video_file.write_bytes(b"\x1aE\xdf\xa3dummy_webm")
    mock_video = MagicMock()
    mock_video.path.return_value = str(dummy_video_file)
    mock_page.video = mock_video

    evidences = manager.on_test_failure(
        job_id="job-e2e-100",
        tenant_id="tenant-alpha",
        criterion_id="AC-01",
        test_name="checkout_flow_spec",
        page_or_context=mock_page,
        out_dir=out_dir,
    )

    # Deben haberse capturado 3 artefactos: screenshot, trace, video
    assert len(evidences) == 3

    types = {ev.file_type for ev in evidences}
    assert types == {"screenshot", "video", "trace"}

    for ev in evidences:
        assert ev.criterion_id == "AC-01"
        assert ev.job_id == "job-e2e-100"
        assert Path(ev.file_path).exists()
        assert ev.created_at is not None

    # Validar llamadas a los métodos de Playwright
    mock_page.screenshot.assert_called_once()
    mock_tracing.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Prueba 2: test_politica_only_on_failure_purga_exitosos
# ---------------------------------------------------------------------------
def test_politica_only_on_failure_purga_exitosos(tmp_path: Path) -> None:
    """
    Prueba 2: Simular una prueba exitosa que dejó archivos temporales en disco.
    Invocar cleanup_success_artifacts y verificar la eliminación correcta.
    """
    manager = PlaywrightHookManager()
    temp_dir = tmp_path / "playwright_temp"
    temp_dir.mkdir()

    # Crear artefactos temporales
    f1 = temp_dir / "temp_video.webm"
    f2 = temp_dir / "trace_temp.zip"
    f3 = temp_dir / "snapshot.png"
    f4 = temp_dir / "keep_notes.txt"  # No debe borrarse

    f1.write_bytes(b"webm")
    f2.write_bytes(b"zip")
    f3.write_bytes(b"png")
    f4.write_text("notas de auditoria", encoding="utf-8")

    deleted = manager.cleanup_success_artifacts(temp_dir)

    # 3 archivos multimedia deben eliminarse
    assert deleted == 3
    assert not f1.exists()
    assert not f2.exists()
    assert not f3.exists()
    assert f4.exists()  # Archivo no perteneciente a las extensiones temporales


# ---------------------------------------------------------------------------
# Prueba 3: test_prevencion_path_traversal_en_rutas_de_evidencia
# ---------------------------------------------------------------------------
def test_prevencion_path_traversal_en_rutas_de_evidencia(tmp_path: Path) -> None:
    """
    Prueba 3: Validar que resolve_safe_path detecte y bloquee intentos de Path Traversal
    en tenant_id, job_id, criterion_id o en outputDir (QA-F03).
    """
    manager = PlaywrightHookManager()
    safe_base = tmp_path / "safe_dir"
    safe_base.mkdir()

    # 1. Path traversal en generate_playwright_config_dict
    with pytest.raises(ValueError, match="Intento de Path-Traversal"):
        manager.generate_playwright_config_dict(
            job_id="../../etc",
            tenant_id="malicious_tenant",
            out_dir=safe_base,
        )

    # 2. Path traversal en on_test_failure
    mock_page = MagicMock()
    with pytest.raises(ValueError, match="Intento de Path-Traversal"):
        manager.on_test_failure(
            job_id="job-1",
            tenant_id="../../etc",
            criterion_id="AC-01",
            test_name="exploit",
            page_or_context=mock_page,
            out_dir=safe_base,
        )


# ---------------------------------------------------------------------------
# Prueba 4: test_generacion_configuracion_playwright_valida
# ---------------------------------------------------------------------------
def test_generacion_configuracion_playwright_valida(tmp_path: Path) -> None:
    """
    Prueba 4: Verificar la estructura emitida por generate_playwright_config_dict
    con las políticas 'only-on-failure' y 'retain-on-failure'.
    """
    manager = PlaywrightHookManager()
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()

    cfg = manager.generate_playwright_config_dict(
        job_id="job-cfg-42",
        tenant_id="tenant-beta",
        out_dir=out_dir,
    )

    assert "outputDir" in cfg
    assert "tenant-beta/job-cfg-42" in cfg["outputDir"]
    assert cfg["use"]["screenshot"] == "only-on-failure"
    assert cfg["use"]["video"] == "retain-on-failure"
    assert cfg["use"]["trace"] == "retain-on-failure"
    assert cfg["metadata"]["job_id"] == "job-cfg-42"
    assert cfg["metadata"]["tenant_id"] == "tenant-beta"


# ---------------------------------------------------------------------------
# Prueba 5: test_inmutabilidad_dtos_y_casos_borde
# ---------------------------------------------------------------------------
def test_inmutabilidad_dtos_y_casos_borde(tmp_path: Path) -> None:
    """
    Prueba 5: Validar inmutabilidad Pydantic (frozen=True), modos condicionales 'off'
    y tolerancia ante páginas sin video o tracing.
    """
    # 1. Inmutabilidad DTO
    item = CapturedEvidenceItemDTO(
        criterion_id="AC-05",
        job_id="job-freeze",
        file_type="screenshot",
        file_path="/tmp/test.png",
        created_at="2026-11-05T20:00:00Z",
    )
    with pytest.raises(ValidationError):
        item.file_type = "video"

    # 2. Configuración con modos 'off'
    off_config = PlaywrightHookConfig(
        screenshot_mode="off",
        video_mode="off",
        trace_mode="off",
    )
    off_manager = PlaywrightHookManager(config=off_config)

    empty_page = MagicMock()
    evidences = off_manager.on_test_failure(
        job_id="job-off",
        tenant_id="tenant-off",
        criterion_id="AC-99",
        test_name="disabled_capture",
        page_or_context=empty_page,
        out_dir=tmp_path,
    )
    assert len(evidences) == 0

    # 3. Limpieza en directorio inexistente retorna 0 sin fallar
    missing_dir = tmp_path / "non_existent_dir"
    assert off_manager.cleanup_success_artifacts(missing_dir) == 0

    # 4. Limpieza sobre archivo individual
    single_file = tmp_path / "single_artifact.webm"
    single_file.write_bytes(b"webm")
    assert off_manager.cleanup_success_artifacts(single_file) == 1
    assert not single_file.exists()


# ---------------------------------------------------------------------------
# Prueba 6: test_cobertura_browser_context_video_save_as_y_excepciones
# ---------------------------------------------------------------------------
def test_cobertura_browser_context_video_save_as_y_excepciones(tmp_path: Path) -> None:
    """
    Prueba 6: Cobertura de BrowserContext directo, video con save_as,
    manejo defensivo ante excepciones en captura y archivos omitidos en limpieza.
    """
    manager = PlaywrightHookManager()
    out_dir = tmp_path / "artifacts_adv"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Objeto Page con video.save_as()
    mock_page = MagicMock()
    mock_video = MagicMock()

    # video con save_as que escribe el archivo
    def fake_save_as(path_str: str) -> None:
        Path(path_str).write_bytes(b"webm_save_as_bytes")

    mock_video.save_as = fake_save_as
    del mock_video.path  # No tiene path(), solo save_as
    mock_page.video = mock_video

    # BrowserContext directo que tiene pages=[mock_page]
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    del mock_context.screenshot  # El context no tiene screenshot directamente
    mock_tracing = MagicMock()
    mock_context.tracing = mock_tracing

    evidences = manager.on_test_failure(
        job_id="job-ctx-1",
        tenant_id="tenant-ctx",
        criterion_id="AC-02",
        test_name="context_spec",
        page_or_context=mock_context,
        out_dir=out_dir,
    )
    assert len(evidences) == 3

    # 2. Excepciones en captura registradas como advertencia sin romper ejecución
    failing_page = MagicMock()
    failing_page.screenshot.side_effect = RuntimeError("Screenshot driver crash")
    failing_context = MagicMock()
    failing_context.tracing.stop.side_effect = RuntimeError("Tracing buffer corrupt")
    failing_page.context = failing_context
    failing_page.video = MagicMock(side_effect=RuntimeError("Video encoder crashed"))

    # No debe propagar excepciones al llamador
    ev_fail = manager.on_test_failure(
        job_id="job-fail-1",
        tenant_id="tenant-fail",
        criterion_id="AC-03",
        test_name="crash_test",
        page_or_context=failing_page,
        out_dir=out_dir,
    )
    assert len(ev_fail) == 0

    # 3. Limpieza de archivo individual que no coincide con extensiones
    non_target_file = tmp_path / "sample.pdf"
    non_target_file.write_bytes(b"pdf")
    assert manager.cleanup_success_artifacts(non_target_file) == 0
    assert non_target_file.exists()

    # 4. Objeto envoltorio con atributo .page
    class PageWrapper:
        def __init__(self, inner: MagicMock) -> None:
            self.page = inner

    wrapped = PageWrapper(mock_page)
    ev_wrap = manager.on_test_failure(
        job_id="job-wrap",
        tenant_id="tenant-wrap",
        criterion_id="AC-04",
        test_name="wrapped_test",
        page_or_context=wrapped,
        out_dir=out_dir,
    )
    assert len(ev_wrap) == 3

    # 5. Excepción al borrar archivo en cleanup_success_artifacts
    unwritable = tmp_path / "unwritable.png"
    unwritable.write_bytes(b"locked")
    with pytest.MonkeyPatch.context() as mp:

        def failing_unlink(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError("Access denied")

        mp.setattr(Path, "unlink", failing_unlink)
        cleaned = manager.cleanup_success_artifacts(unwritable.parent)
        assert cleaned == 0
