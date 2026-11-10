"""Pruebas unitarias exhaustivas para PerformanceReporter y DTOs de Web Vitals.

Ticket: KC-QA-B5-12
Épica: EQA5 - Motor de Pruebas Autónomo y E2E
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.performance_reporter import (
    PerformanceBudgetExceededError,
    PerformanceReportDTO,
    PerformanceReporter,
    WebVitalsMetricDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_clean_metrics_dict() -> dict[str, Any]:
    return {
        "metrics": [
            {
                "view_name": "login",
                "ttfb_ms": 120.5,
                "fcp_ms": 350.2,
                "lcp_ms": 600.0,
                "cls": 0.01,
                "dom_interactive_ms": 280.0,
            },
            {
                "view_name": "kanban",
                "ttfb_ms": 180.0,
                "fcp_ms": 420.0,
                "lcp_ms": 850.0,
                "cls": 0.02,
                "dom_interactive_ms": 390.0,
            },
            {
                "view_name": "detalle_ticket",
                "ttfb_ms": 150.0,
                "fcp_ms": 500.0,
                "lcp_ms": 950.0,
                "cls": 0.03,
                "dom_interactive_ms": 410.0,
            },
        ]
    }


def test_web_vitals_dto_immutability() -> None:
    """Verifica que WebVitalsMetricDTO sea inmutable (frozen=True)."""
    dto = WebVitalsMetricDTO(
        view_name="login",
        ttfb_ms=100.0,
        fcp_ms=300.0,
        lcp_ms=500.0,
        cls=0.01,
        dom_interactive_ms=250.0,
        passed=True,
    )

    with pytest.raises(ValidationError):
        dto.passed = False


def test_validate_metric_clean_passes() -> None:
    """Verifica que métricas dentro de todos los umbrales pasen sin violaciones."""
    metric = PerformanceReporter.validate_metric(
        {
            "view_name": "login",
            "ttfb_ms": 150.0,
            "fcp_ms": 400.0,
            "lcp_ms": 800.0,
            "cls": 0.02,
            "dom_interactive_ms": 300.0,
        }
    )

    assert metric.passed is True
    assert len(metric.violations) == 0
    assert metric.view_name == "login"


def test_validate_metric_ttfb_exceeded() -> None:
    """Verifica detección de TTFB > 600ms."""
    metric = PerformanceReporter.validate_metric(
        {
            "view_name": "slow_network",
            "ttfb_ms": 650.0,
            "fcp_ms": 700.0,
            "lcp_ms": 900.0,
            "cls": 0.0,
            "dom_interactive_ms": 680.0,
        }
    )

    assert metric.passed is False
    assert any("TTFB excesivo" in v for v in metric.violations)


def test_validate_metric_fcp_exceeded() -> None:
    """Verifica detección de FCP >= 1200ms (criterio duro)."""
    metric = PerformanceReporter.validate_metric(
        {
            "view_name": "slow_paint",
            "ttfb_ms": 200.0,
            "fcp_ms": 1200.0,
            "lcp_ms": 1400.0,
            "cls": 0.01,
            "dom_interactive_ms": 800.0,
        }
    )

    assert metric.passed is False
    assert any("FCP excesivo" in v for v in metric.violations)


def test_validate_metric_lcp_exceeded() -> None:
    """Verifica detección de LCP > 2000ms."""
    metric = PerformanceReporter.validate_metric(
        {
            "view_name": "heavy_asset",
            "ttfb_ms": 200.0,
            "fcp_ms": 500.0,
            "lcp_ms": 2100.0,
            "cls": 0.01,
            "dom_interactive_ms": 600.0,
        }
    )

    assert metric.passed is False
    assert any("LCP excesivo" in v for v in metric.violations)


def test_validate_metric_cls_exceeded() -> None:
    """Verifica detección de CLS > 0.1."""
    metric = PerformanceReporter.validate_metric(
        {
            "view_name": "unstable_layout",
            "ttfb_ms": 200.0,
            "fcp_ms": 400.0,
            "lcp_ms": 800.0,
            "cls": 0.15,
            "dom_interactive_ms": 500.0,
        }
    )

    assert metric.passed is False
    assert any("CLS excesivo" in v for v in metric.violations)


def test_validate_metric_from_dto_instance() -> None:
    """Verifica el soporte para validar a partir de una instancia DTO previa."""
    initial_dto = WebVitalsMetricDTO(
        view_name="revalidated",
        ttfb_ms=100.0,
        fcp_ms=250.0,
        lcp_ms=450.0,
        cls=0.005,
        dom_interactive_ms=200.0,
        passed=True,
    )
    metric = PerformanceReporter.validate_metric(initial_dto)
    assert metric.passed is True
    assert metric.view_name == "revalidated"


def test_validate_metric_invalid_type_raises() -> None:
    """Verifica que pasar tipos incompatibles a validate_metric lance TypeError."""
    with pytest.raises(TypeError, match="Formato inválido"):
        invalid_metric: Any = "no_es_un_diccionario"
        PerformanceReporter.validate_metric(invalid_metric)


def test_parse_playwright_results_dict_input(
    sample_clean_metrics_dict: dict[str, Any],
) -> None:
    """Verifica el parseo desde un diccionario contenedor."""
    report = PerformanceReporter.parse_playwright_results(sample_clean_metrics_dict)
    assert report.total_views_tested == 3
    assert report.is_conforming is True
    assert len(report.metrics) == 3
    assert report.report_id.startswith("perf-")


def test_parse_playwright_results_list_input(
    sample_clean_metrics_dict: dict[str, Any],
) -> None:
    """Verifica el parseo desde una lista directa."""
    raw_list = sample_clean_metrics_dict["metrics"]
    report = PerformanceReporter.parse_playwright_results(raw_list)
    assert report.total_views_tested == 3
    assert report.is_conforming is True


def test_parse_playwright_results_invalid_type() -> None:
    """Verifica que tipos no soportados en parse_playwright_results lancen TypeError."""
    with pytest.raises(TypeError, match="Tipo de dato no soportado"):
        invalid_input: Any = 98765
        PerformanceReporter.parse_playwright_results(invalid_input)


def test_assert_zero_budget_violations_clean(
    sample_clean_metrics_dict: dict[str, Any],
) -> None:
    """Verifica que assert_zero_budget_violations no lance excepción en reportes conformes."""
    report = PerformanceReporter.parse_playwright_results(sample_clean_metrics_dict)
    PerformanceReporter.assert_zero_budget_violations(report)


def test_assert_zero_budget_violations_raises() -> None:
    """Verifica que assert_zero_budget_violations lance PerformanceBudgetExceededError ante desvíos."""
    failing_report = PerformanceReportDTO(
        report_id="perf-fail-test",
        total_views_tested=2,
        is_conforming=False,
        metrics=[
            WebVitalsMetricDTO(
                view_name="dashboard_lento",
                ttfb_ms=750.0,
                fcp_ms=1300.0,
                lcp_ms=2500.0,
                cls=0.2,
                dom_interactive_ms=900.0,
                passed=False,
                violations=[
                    "TTFB excesivo: 750.00ms > 600.00ms",
                    "FCP excesivo: 1300.00ms >= 1200.00ms",
                ],
            ),
            WebVitalsMetricDTO(
                view_name="login_rapido",
                ttfb_ms=100.0,
                fcp_ms=300.0,
                lcp_ms=500.0,
                cls=0.01,
                dom_interactive_ms=250.0,
                passed=True,
                violations=[],
            ),
        ],
    )

    with pytest.raises(PerformanceBudgetExceededError) as exc_info:
        PerformanceReporter.assert_zero_budget_violations(failing_report)

    error_msg = str(exc_info.value)
    assert "dashboard_lento" in error_msg
    assert "TTFB excesivo" in error_msg
    assert "FCP excesivo" in error_msg


def test_export_and_load_report_json(tmp_path: Path) -> None:
    """Verifica el ciclo de exportación a JSON y carga/validación con Pydantic."""
    report = PerformanceReportDTO(
        report_id="perf-roundtrip",
        total_views_tested=1,
        is_conforming=True,
        metrics=[
            WebVitalsMetricDTO(
                view_name="login",
                ttfb_ms=120.0,
                fcp_ms=350.0,
                lcp_ms=550.0,
                cls=0.01,
                dom_interactive_ms=280.0,
                passed=True,
            )
        ],
    )

    target_file = tmp_path / "artifacts" / "performance-vitals-report.json"
    exported_path = PerformanceReporter.export_report_json(report, target_file)

    assert exported_path.exists()
    assert exported_path == target_file.resolve()

    loaded = PerformanceReporter.load_report_json(exported_path)
    assert loaded.report_id == "perf-roundtrip"
    assert loaded.total_views_tested == 1
    assert loaded.is_conforming is True
    assert loaded.metrics[0].view_name == "login"


def test_load_report_json_not_found(tmp_path: Path) -> None:
    """Verifica que cargar un archivo inexistente lance FileNotFoundError."""
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError, match="El archivo de reporte no existe"):
        PerformanceReporter.load_report_json(missing)


def test_cli_main_success_and_failures(tmp_path: Path) -> None:
    """Verifica las rutas de éxito y fallo del CLI interactivo."""
    ok_report = tmp_path / "ok.json"
    ok_report.write_text(
        json.dumps(
            {
                "report_id": "perf-cli-ok",
                "created_at": "2026-11-09T20:00:00Z",
                "total_views_tested": 1,
                "is_conforming": True,
                "metrics": [
                    {
                        "view_name": "login",
                        "ttfb_ms": 100.0,
                        "fcp_ms": 300.0,
                        "lcp_ms": 500.0,
                        "cls": 0.01,
                        "dom_interactive_ms": 250.0,
                        "passed": True,
                        "violations": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. Ejecución informativa (sin check)
    assert main(["--report-file", str(ok_report)]) == 0

    # 2. Ejecución con compuerta estricta exitosa
    assert main(["--report-file", str(ok_report), "--check-budget"]) == 0

    # 3. Ejecución con compuerta estricta fallida
    fail_report = tmp_path / "fail.json"
    fail_report.write_text(
        json.dumps(
            {
                "report_id": "perf-cli-fail",
                "created_at": "2026-11-09T20:00:00Z",
                "total_views_tested": 1,
                "is_conforming": False,
                "metrics": [
                    {
                        "view_name": "slow_view",
                        "ttfb_ms": 800.0,
                        "fcp_ms": 1500.0,
                        "lcp_ms": 2500.0,
                        "cls": 0.25,
                        "dom_interactive_ms": 900.0,
                        "passed": False,
                        "violations": ["FCP excesivo"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["--report-file", str(fail_report), "--check-budget"]) == 1

    # 4. Archivo inexistente -> código fatal 2
    assert main(["--report-file", str(tmp_path / "no_existe.json")]) == 2
