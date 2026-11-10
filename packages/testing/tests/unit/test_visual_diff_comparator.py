"""Pruebas unitarias para VisualDiffComparator (KC-QA-B5-04).

Valida la comparación pixel a pixel, detección de divergencias, generación de diff.png,
inmutabilidad de DTOs (frozen=True) y manejo de reportes estructurados para EvidenceSealer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.visual_diff_comparator import (
    VisualDiffComparator,
    VisualDiffMetricDTO,
    VisualDiffReportDTO,
    VisualRegressionError,
)
from PIL import Image
from pydantic import ValidationError


@pytest.fixture
def temp_images_dir(tmp_path: Path) -> Path:
    """Directorio temporal para imágenes de prueba."""
    img_dir = tmp_path / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    return img_dir


def _crear_imagen(
    path: Path, color: tuple[int, int, int, int], size: tuple[int, int] = (100, 100)
) -> Path:
    """Crea una imagen RGBA uniforme para pruebas."""
    img = Image.new("RGBA", size, color)
    img.save(path, "PNG")
    return path


def test_identical_images_pass(temp_images_dir: Path) -> None:
    """Verifica que dos imágenes idénticas den 0% de diferencia y passed=True."""
    baseline = _crear_imagen(temp_images_dir / "base.png", (100, 150, 200, 255))
    runtime = _crear_imagen(temp_images_dir / "run.png", (100, 150, 200, 255))

    comparator = VisualDiffComparator(threshold=0.01)
    metric = comparator.compare_images(baseline, runtime, view_name="test_view")

    assert metric.view_name == "test_view"
    assert metric.total_pixels == 100 * 100
    assert metric.mismatch_pixels == 0
    assert metric.diff_percentage == 0.0
    assert metric.passed is True
    assert metric.diff_path is None


def test_divergent_images_generates_diff_png(temp_images_dir: Path) -> None:
    """Verifica que divergencias generen diff.png y calculen diff_percentage correctamente."""
    baseline = _crear_imagen(temp_images_dir / "base.png", (255, 255, 255, 255), (100, 100))

    # Crear runtime con una región alterada de 20x20 píxeles (400 píxeles = 4% de 10,000)
    run_img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(20):
        for y in range(20):
            run_img.putpixel((x, y), (0, 0, 0, 255))
    runtime = temp_images_dir / "run.png"
    run_img.save(runtime, "PNG")

    diff_path = temp_images_dir / "diff.png"
    comparator = VisualDiffComparator(threshold=0.01)  # 1.0% de tolerancia máxima
    metric = comparator.compare_images(
        baseline, runtime, output_diff=diff_path, view_name="kanban_board"
    )

    assert metric.mismatch_pixels == 400
    assert metric.diff_percentage == 4.0
    assert metric.passed is False  # 4.0% > 1.0%
    assert metric.diff_path == str(diff_path.resolve())
    assert diff_path.exists()

    # Validar que diff.png tenga píxeles magenta (#FF00FF)
    diff_loaded = Image.open(diff_path).convert("RGBA")
    magenta_pixel = diff_loaded.getpixel((5, 5))
    assert magenta_pixel == (255, 0, 255, 255)


def test_assert_no_visual_regressions_raises_on_failure(temp_images_dir: Path) -> None:
    """Verifica que assert_no_visual_regressions lance VisualRegressionError ante regresión."""
    comparator = VisualDiffComparator(threshold=0.01)

    failing_metric = VisualDiffMetricDTO(
        view_name="login_view",
        baseline_path=str(temp_images_dir / "base.png"),
        runtime_path=str(temp_images_dir / "run.png"),
        diff_path=str(temp_images_dir / "diff.png"),
        total_pixels=10000,
        mismatch_pixels=250,
        diff_percentage=2.5,
        passed=False,
    )
    report = comparator.build_report([failing_metric])

    assert report.has_regressions is True
    with pytest.raises(VisualRegressionError) as exc_info:
        comparator.assert_no_visual_regressions(report)

    assert "Visual regression detected in 1 view(s)" in str(exc_info.value)
    assert "login_view" in str(exc_info.value)
    assert "2.5000%" in str(exc_info.value)


def test_assert_no_visual_regressions_passes_clean_report() -> None:
    """Verifica que un reporte sin regresiones pase la aserción sin excepciones."""
    comparator = VisualDiffComparator(threshold=0.01)

    passing_metric = VisualDiffMetricDTO(
        view_name="dashboard",
        baseline_path="/path/base.png",
        runtime_path="/path/run.png",
        diff_path=None,
        total_pixels=10000,
        mismatch_pixels=10,
        diff_percentage=0.1,
        passed=True,
    )
    report = comparator.build_report([passing_metric])

    assert report.has_regressions is False
    # No debe levantar ninguna excepción
    comparator.assert_no_visual_regressions(report)


def test_inmutabilidad_dtos_regresion_visual() -> None:
    """Verifica que VisualDiffMetricDTO y VisualDiffReportDTO mantengan frozen=True."""
    metric = VisualDiffMetricDTO(
        view_name="modal_gate",
        baseline_path="/base.png",
        runtime_path="/run.png",
        diff_path=None,
        total_pixels=1000,
        mismatch_pixels=0,
        diff_percentage=0.0,
        passed=True,
    )

    with pytest.raises(ValidationError):
        metric.passed = False

    with pytest.raises(ValidationError):
        metric.view_name = "nuevo_nombre"

    report = VisualDiffReportDTO(
        report_id="vdiff-test-01",
        created_at="2026-11-09T12:00:00Z",
        threshold_applied=0.01,
        metrics=[metric],
        has_regressions=False,
    )

    with pytest.raises(ValidationError):
        report.has_regressions = True

    with pytest.raises(ValidationError):
        report.report_id = "vdiff-otro"


def test_export_and_load_report_json(temp_images_dir: Path) -> None:
    """Verifica la serialización y recarga determinista de reportes JSON."""
    comparator = VisualDiffComparator(threshold=0.02)
    metric = VisualDiffMetricDTO(
        view_name="view_export",
        baseline_path=str(temp_images_dir / "base.png"),
        runtime_path=str(temp_images_dir / "run.png"),
        diff_path=None,
        total_pixels=5000,
        mismatch_pixels=25,
        diff_percentage=0.5,
        passed=True,
    )
    report = comparator.build_report([metric], report_id="vdiff-custom-001")

    json_path = temp_images_dir / "reports" / "visual-diff-report.json"
    exported_path = comparator.export_report_json(report, json_path)

    assert exported_path.exists()
    assert exported_path == json_path.resolve()

    loaded_report = comparator.load_report_json(json_path)
    assert loaded_report.report_id == "vdiff-custom-001"
    assert loaded_report.threshold_applied == 0.02
    assert loaded_report.has_regressions is False
    assert len(loaded_report.metrics) == 1
    assert loaded_report.metrics[0].view_name == "view_export"
    assert loaded_report.metrics[0].diff_percentage == 0.5


def test_dimension_mismatch_handling(temp_images_dir: Path) -> None:
    """Verifica que discrepancias de tamaño sean manejadas con lienzo unificado sin crash."""
    baseline = _crear_imagen(temp_images_dir / "base_small.png", (255, 0, 0, 255), (50, 50))
    runtime = _crear_imagen(temp_images_dir / "run_large.png", (255, 0, 0, 255), (100, 100))

    comparator = VisualDiffComparator(threshold=0.01)
    metric = comparator.compare_images(baseline, runtime, view_name="dimension_test")

    # Lienzo máximo: 100x100 = 10,000 píxeles. La región no cubierta (7,500 px) difiere.
    assert metric.total_pixels == 10000
    assert metric.mismatch_pixels > 0
    assert metric.passed is False


def test_custom_threshold_configuration(temp_images_dir: Path) -> None:
    """Verifica que umbrales personalizados cambien el veredicto passed."""
    baseline = _crear_imagen(temp_images_dir / "base.png", (255, 255, 255, 255), (100, 100))

    # Crear runtime con 2% de diferencia (200 píxeles)
    run_img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(20):
        for y in range(10):
            run_img.putpixel((x, y), (0, 0, 0, 255))
    runtime = temp_images_dir / "run.png"
    run_img.save(runtime, "PNG")

    # Con umbral estricto 0.01 (1.0%): debe fallar
    comp_strict = VisualDiffComparator(threshold=0.01)
    m_strict = comp_strict.compare_images(baseline, runtime)
    assert m_strict.diff_percentage == 2.0
    assert m_strict.passed is False

    # Con umbral permisivo 0.05 (5.0%): debe pasar
    comp_lenient = VisualDiffComparator(threshold=0.05)
    m_lenient = comp_lenient.compare_images(baseline, runtime)
    assert m_lenient.diff_percentage == 2.0
    assert m_lenient.passed is True


def test_missing_file_raises_filenotfound(temp_images_dir: Path) -> None:
    """Verifica que rutas inexistentes levanten FileNotFoundError."""
    valid_file = _crear_imagen(temp_images_dir / "valid.png", (0, 0, 0, 255))
    missing_file = temp_images_dir / "no_existe.png"

    comparator = VisualDiffComparator()

    with pytest.raises(FileNotFoundError, match="Baseline image not found"):
        comparator.compare_images(missing_file, valid_file)

    with pytest.raises(FileNotFoundError, match="Runtime image not found"):
        comparator.compare_images(valid_file, missing_file)

    with pytest.raises(FileNotFoundError, match="Report file not found"):
        comparator.load_report_json(missing_file)


def test_invalid_json_raises_typeerror(temp_images_dir: Path) -> None:
    """Verifica que JSON con estructura no-diccionario lance TypeError."""
    bad_json = temp_images_dir / "bad.json"
    bad_json.write_text('["un", "array"]', encoding="utf-8")

    comparator = VisualDiffComparator()
    with pytest.raises(TypeError, match="Formato JSON inválido"):
        comparator.load_report_json(bad_json)


def test_cli_main_success_and_failure(
    temp_images_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valida la ejecución de la CLI main() tanto para éxito como ante regresión."""
    from kallicode_testing.visual_diff_comparator import main

    base = _crear_imagen(temp_images_dir / "cli_base.png", (200, 200, 200, 255))
    run = _crear_imagen(temp_images_dir / "cli_run.png", (200, 200, 200, 255))
    diff = temp_images_dir / "cli_diff.png"
    report_out = temp_images_dir / "cli_report.json"

    # Caso 1: Imágenes idénticas -> exit 0
    test_args_success = [
        "visual_diff_comparator.py",
        "--baseline",
        str(base),
        "--runtime",
        str(run),
        "--diff",
        str(diff),
        "--report-out",
        str(report_out),
    ]
    monkeypatch.setattr("sys.argv", test_args_success)
    assert main() == 0
    assert report_out.exists()

    # Caso 2: Imágenes divergentes superando umbral estricto -> exit 1
    run_div = Image.new("RGBA", (100, 100), (0, 0, 0, 255))
    run_div_path = temp_images_dir / "cli_div.png"
    run_div.save(run_div_path, "PNG")

    test_args_fail = [
        "visual_diff_comparator.py",
        "--baseline",
        str(base),
        "--runtime",
        str(run_div_path),
        "--threshold",
        "0.001",
    ]
    monkeypatch.setattr("sys.argv", test_args_fail)
    assert main() == 1
