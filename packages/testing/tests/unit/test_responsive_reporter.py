"""Pruebas unitarias exhaustivas para ResponsiveReporter y DTOs de layout responsivo.

Ticket: KC-QA-B5-09
Épica: EQA4 / EQA5 - Motor de Pruebas Autónomo y E2E
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.responsive_reporter import (
    ResponsiveAuditReportDTO,
    ResponsiveLayoutError,
    ResponsiveReporter,
    ViewportResultDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_valid_playwright_results() -> list[dict[str, Any]]:
    return [
        {
            "view_name": "login",
            "viewport_label": "desktop",
            "width": 1920,
            "height": 1080,
            "has_horizontal_overflow": False,
            "sidebar_collapsed": False,
            "passed": True,
        },
        {
            "view_name": "kanban",
            "viewport_label": "tablet",
            "width": 768,
            "height": 1024,
            "has_horizontal_overflow": False,
            "sidebar_collapsed": True,
            "passed": True,
        },
        {
            "view_name": "nuevo-ticket",
            "viewport_label": "mobile",
            "width": 375,
            "height": 812,
            "has_horizontal_overflow": False,
            "sidebar_collapsed": True,
            "passed": True,
        },
    ]


def test_viewport_result_dto_immutability() -> None:
    """Verifica que ViewportResultDTO sea inmutable (frozen=True)."""
    dto = ViewportResultDTO(
        view_name="dashboard",
        viewport_label="desktop",
        width=1920,
        height=1080,
        has_horizontal_overflow=False,
        sidebar_collapsed=False,
        passed=True,
    )

    with pytest.raises(ValidationError):
        # Intentar modificar un atributo en un modelo frozen
        dto.passed = False


def test_parse_playwright_results_success(
    sample_valid_playwright_results: list[dict[str, Any]],
) -> None:
    """Verifica el parseo correcto de resultados conformes sin desbordamiento."""
    report = ResponsiveReporter.parse_playwright_results(sample_valid_playwright_results)

    assert report.total_scenarios == 3
    assert report.failed_scenarios == 0
    assert report.is_conforming is True
    assert len(report.results) == 3
    assert report.report_id.startswith("resp-")
    assert report.results[0].viewport_label == "desktop"
    assert report.results[1].viewport_label == "tablet"
    assert report.results[2].viewport_label == "mobile"


def test_parse_playwright_results_with_overflow_and_failures() -> None:
    """Verifica que el desbordamiento horizontal invalide el escenario."""
    raw_data: list[dict[str, Any]] = [
        {
            "view_name": "aprobaciones",
            "viewport_label": "mobile",
            "width": 375,
            "height": 812,
            "has_horizontal_overflow": True,
            "sidebar_collapsed": True,
            "passed": True,  # Aunque venga passed=True, has_overflow lo invalida
        },
        {
            "view_name": "conexiones",
            "viewport_label": "desktop",
            "width": 1920,
            "height": 1080,
            "has_horizontal_overflow": False,
            "sidebar_collapsed": False,
            "passed": False,
            "error_message": "Botón fuera de marco",
        },
    ]

    report = ResponsiveReporter.parse_playwright_results(raw_data)

    assert report.total_scenarios == 2
    assert report.failed_scenarios == 2
    assert report.is_conforming is False
    assert report.results[0].has_horizontal_overflow is True
    assert report.results[0].passed is False
    assert report.results[1].passed is False


def test_parse_playwright_results_with_dto_instances() -> None:
    """Verifica el soporte para recibir directamente instancias de ViewportResultDTO."""
    dto1 = ViewportResultDTO(
        view_name="login",
        viewport_label="desktop",
        width=1920,
        height=1080,
        has_horizontal_overflow=False,
        sidebar_collapsed=False,
        passed=True,
    )
    report = ResponsiveReporter.parse_playwright_results([dto1])
    assert report.total_scenarios == 1
    assert report.failed_scenarios == 0
    assert report.is_conforming is True


def test_parse_playwright_results_invalid_type() -> None:
    """Verifica que pasar tipos incompatibles lance TypeError."""
    invalid_data: Any = ["cadena_invalida"]
    with pytest.raises(TypeError, match="Tipo de dato no soportado"):
        ResponsiveReporter.parse_playwright_results(invalid_data)


def test_parse_playwright_results_fallback_viewport_label() -> None:
    """Verifica que una etiqueta de viewport desconocida use el fallback 'desktop'."""
    raw_data = [
        {
            "view_name": "test_unknown",
            "viewport_label": "smartwatch",
            "width": 200,
            "height": 200,
            "has_horizontal_overflow": False,
            "sidebar_collapsed": True,
            "passed": True,
        }
    ]
    report = ResponsiveReporter.parse_playwright_results(raw_data)
    assert report.results[0].viewport_label == "desktop"


def test_assert_no_responsive_failures_passes(
    sample_valid_playwright_results: list[dict[str, Any]],
) -> None:
    """Verifica que assert_no_responsive_failures no lance excepción en reportes conformes."""
    report = ResponsiveReporter.parse_playwright_results(sample_valid_playwright_results)
    # No debe levantar ninguna excepción
    ResponsiveReporter.assert_no_responsive_failures(report)


def test_assert_no_responsive_failures_raises() -> None:
    """Verifica que assert_no_responsive_failures lance ResponsiveLayoutError con detalles claros."""
    failing_report = ResponsiveAuditReportDTO(
        report_id="resp-test-fail",
        total_scenarios=2,
        failed_scenarios=2,
        is_conforming=False,
        results=[
            ViewportResultDTO(
                view_name="tickets",
                viewport_label="mobile",
                width=375,
                height=812,
                has_horizontal_overflow=True,
                sidebar_collapsed=True,
                passed=False,
            ),
            ViewportResultDTO(
                view_name="aprobaciones",
                viewport_label="tablet",
                width=768,
                height=1024,
                has_horizontal_overflow=False,
                sidebar_collapsed=True,
                passed=False,
                error_message="Error de alineación",
            ),
            ViewportResultDTO(
                view_name="kanban",
                viewport_label="desktop",
                width=1920,
                height=1080,
                has_horizontal_overflow=False,
                sidebar_collapsed=False,
                passed=False,
                # Sin error_message ni overflow para probar fallback "Estado no conforme"
            ),
            ViewportResultDTO(
                view_name="login",
                viewport_label="desktop",
                width=1920,
                height=1080,
                has_horizontal_overflow=False,
                sidebar_collapsed=False,
                passed=True,
            ),
        ],
    )

    with pytest.raises(ResponsiveLayoutError) as exc_info:
        ResponsiveReporter.assert_no_responsive_failures(failing_report)

    error_msg = str(exc_info.value)
    assert "Se detectaron 2 escenarios responsivos no conformes" in error_msg
    assert "Desbordamiento horizontal involuntario detectado" in error_msg
    assert "Error de alineación" in error_msg
    assert "Estado no conforme" in error_msg


def test_export_and_load_report_json(tmp_path: Path) -> None:
    """Verifica el ciclo completo de exportación a JSON y carga/validación con Pydantic."""
    report = ResponsiveAuditReportDTO(
        report_id="resp-roundtrip",
        total_scenarios=1,
        failed_scenarios=0,
        is_conforming=True,
        results=[
            ViewportResultDTO(
                view_name="kanban",
                viewport_label="desktop",
                width=1920,
                height=1080,
                has_horizontal_overflow=False,
                sidebar_collapsed=False,
                passed=True,
            )
        ],
    )

    output_file = tmp_path / "subfolder" / "responsive-report.json"
    exported_path = ResponsiveReporter.export_report_json(report, output_file)

    assert exported_path.exists()
    assert exported_path == output_file.resolve()

    # Cargar y verificar
    loaded_report = ResponsiveReporter.load_report_json(exported_path)
    assert loaded_report.report_id == "resp-roundtrip"
    assert loaded_report.total_scenarios == 1
    assert loaded_report.is_conforming is True
    assert len(loaded_report.results) == 1
    assert loaded_report.results[0].view_name == "kanban"


def test_load_report_json_not_found(tmp_path: Path) -> None:
    """Verifica que intentar cargar un archivo inexistente lance FileNotFoundError."""
    missing_file = tmp_path / "inexistente.json"
    with pytest.raises(FileNotFoundError, match="El archivo de reporte no existe"):
        ResponsiveReporter.load_report_json(missing_file)


def test_cli_main_success(tmp_path: Path) -> None:
    """Verifica la ejecución exitosa del CLI sin compuerta estricta."""
    report_file = tmp_path / "report.json"
    report_file.write_text(
        json.dumps(
            {
                "report_id": "resp-cli-ok",
                "created_at": "2026-11-09T12:00:00Z",
                "total_scenarios": 1,
                "failed_scenarios": 0,
                "is_conforming": True,
                "results": [
                    {
                        "view_name": "login",
                        "viewport_label": "desktop",
                        "width": 1920,
                        "height": 1080,
                        "has_horizontal_overflow": False,
                        "sidebar_collapsed": False,
                        "passed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = main(["--report-file", str(report_file)])
    assert code == 0


def test_cli_main_check_failures_success(tmp_path: Path) -> None:
    """Verifica que el CLI con --check-failures retorne 0 si el reporte es conforme."""
    report_file = tmp_path / "report.json"
    report_file.write_text(
        json.dumps(
            {
                "report_id": "resp-cli-check-ok",
                "created_at": "2026-11-09T12:00:00Z",
                "total_scenarios": 1,
                "failed_scenarios": 0,
                "is_conforming": True,
                "results": [
                    {
                        "view_name": "kanban",
                        "viewport_label": "tablet",
                        "width": 768,
                        "height": 1024,
                        "has_horizontal_overflow": False,
                        "sidebar_collapsed": True,
                        "passed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = main(["--report-file", str(report_file), "--check-failures"])
    assert code == 0


def test_cli_main_check_failures_error(tmp_path: Path) -> None:
    """Verifica que el CLI con --check-failures retorne 1 ante fallos de layout."""
    report_file = tmp_path / "failing_report.json"
    report_file.write_text(
        json.dumps(
            {
                "report_id": "resp-cli-check-fail",
                "created_at": "2026-11-09T12:00:00Z",
                "total_scenarios": 1,
                "failed_scenarios": 1,
                "is_conforming": False,
                "results": [
                    {
                        "view_name": "tickets",
                        "viewport_label": "mobile",
                        "width": 375,
                        "height": 812,
                        "has_horizontal_overflow": True,
                        "sidebar_collapsed": True,
                        "passed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = main(["--report-file", str(report_file), "--check-failures"])
    assert code == 1


def test_cli_main_fatal_missing_file(tmp_path: Path) -> None:
    """Verifica que el CLI retorne código 2 ante errores fatales como archivo no encontrado."""
    non_existent = tmp_path / "non_existent.json"
    code = main(["--report-file", str(non_existent)])
    assert code == 2
