"""Pruebas unitarias exhaustivas para ResilienceReporter y DTOs de resiliencia del frontend.

Ticket: KC-QA-B5-10
Épica: EQA4 / EQA5 - Motor de Pruebas Autónomo y E2E
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.resilience_reporter import (
    FrontendResilienceError,
    FrontendResilienceReportDTO,
    ResilienceReporter,
    ResilienceScenarioResultDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_valid_scenarios_dict() -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "scenario_id": "scen-login-disconnect",
                "description": "Caída Total de API al Iniciar Sesión",
                "failure_type": "connection_refused",
                "error_boundary_triggered": False,
                "white_screen_detected": False,
                "recovered_successfully": True,
                "passed": True,
            },
            {
                "scenario_id": "scen-kanban-disconnect",
                "description": "Desconexión en Tablero Kanban",
                "failure_type": "timeout",
                "error_boundary_triggered": False,
                "white_screen_detected": False,
                "recovered_successfully": True,
                "passed": True,
            },
            {
                "scenario_id": "scen-gate-503",
                "description": "Manejo de HTTP 503 en Firma de Gates",
                "failure_type": "http_503",
                "error_boundary_triggered": False,
                "white_screen_detected": False,
                "recovered_successfully": True,
                "passed": True,
            },
        ]
    }


def test_resilience_scenario_result_dto_immutability() -> None:
    """Verifica que ResilienceScenarioResultDTO sea inmutable (frozen=True)."""
    dto = ResilienceScenarioResultDTO(
        scenario_id="scen-01",
        description="Prueba inmutabilidad",
        failure_type="connection_refused",
        error_boundary_triggered=False,
        white_screen_detected=False,
        recovered_successfully=True,
        passed=True,
    )

    with pytest.raises(ValidationError):
        dto.passed = False


def test_parse_playwright_results_dict_input_success(
    sample_valid_scenarios_dict: dict[str, Any],
) -> None:
    """Verifica el parseo correcto desde un diccionario contenedor."""
    report = ResilienceReporter.parse_playwright_results(sample_valid_scenarios_dict)

    assert report.total_scenarios == 3
    assert report.passed_scenarios == 3
    assert report.is_conforming is True
    assert len(report.scenarios) == 3
    assert report.report_id.startswith("resil-")
    assert report.scenarios[0].failure_type == "connection_refused"
    assert report.scenarios[1].failure_type == "timeout"
    assert report.scenarios[2].failure_type == "http_503"


def test_parse_playwright_results_list_input_success(
    sample_valid_scenarios_dict: dict[str, Any],
) -> None:
    """Verifica el parseo correcto cuando se provee directamente una lista."""
    scenarios_list = sample_valid_scenarios_dict["scenarios"]
    report = ResilienceReporter.parse_playwright_results(scenarios_list)

    assert report.total_scenarios == 3
    assert report.passed_scenarios == 3
    assert report.is_conforming is True


def test_parse_playwright_results_with_dto_instances() -> None:
    """Verifica que acepte instancias directas de ResilienceScenarioResultDTO."""
    dto = ResilienceScenarioResultDTO(
        scenario_id="scen-direct",
        description="Instancia directa",
        failure_type="flaky_reconnection",
        error_boundary_triggered=False,
        white_screen_detected=False,
        recovered_successfully=True,
        passed=True,
    )
    report = ResilienceReporter.parse_playwright_results([dto])
    assert report.total_scenarios == 1
    assert report.passed_scenarios == 1
    assert report.is_conforming is True


def test_parse_playwright_results_wsod_invalidation() -> None:
    """Verifica que la detección de pantalla blanca (WSOD) invalide de inmediato el escenario."""
    raw_data = [
        {
            "scenario_id": "scen-wsod",
            "description": "Fallo con WSOD catastrófico",
            "failure_type": "http_500",
            "error_boundary_triggered": True,
            "white_screen_detected": True,  # Invalida
            "recovered_successfully": True,
            "passed": True,  # Aunque venga passed=True
        }
    ]

    report = ResilienceReporter.parse_playwright_results(raw_data)
    assert report.total_scenarios == 1
    assert report.passed_scenarios == 0
    assert report.is_conforming is False
    assert report.scenarios[0].passed is False


def test_parse_playwright_results_recovery_failed() -> None:
    """Verifica que la falta de recuperación invalide el escenario."""
    raw_data = [
        {
            "scenario_id": "scen-no-recov",
            "description": "Fallo sin recuperación",
            "failure_type": "timeout",
            "error_boundary_triggered": False,
            "white_screen_detected": False,
            "recovered_successfully": False,
            "passed": True,
        }
    ]

    report = ResilienceReporter.parse_playwright_results(raw_data)
    assert report.total_scenarios == 1
    assert report.passed_scenarios == 0
    assert report.is_conforming is False
    assert report.scenarios[0].passed is False


def test_parse_playwright_results_invalid_type() -> None:
    """Verifica que tipos no soportados lancen TypeError."""
    invalid_input: Any = 12345
    with pytest.raises(TypeError, match="Formato no soportado"):
        ResilienceReporter.parse_playwright_results(invalid_input)

    with pytest.raises(TypeError, match="Elemento inválido"):
        invalid_list: Any = ["cadena_no_valida"]
        ResilienceReporter.parse_playwright_results(invalid_list)


def test_parse_playwright_results_fallback_failure_type() -> None:
    """Verifica que tipos de fallo no reconocidos usen el fallback 'connection_refused'."""
    raw_data = [
        {
            "scenario_id": "scen-unknown-ft",
            "description": "Tipo de fallo desconocido",
            "failure_type": "quantum_fluctuation",
            "error_boundary_triggered": False,
            "white_screen_detected": False,
            "recovered_successfully": True,
            "passed": True,
        }
    ]
    report = ResilienceReporter.parse_playwright_results(raw_data)
    assert report.scenarios[0].failure_type == "connection_refused"


def test_assert_zero_resilience_failures_clean_report(
    sample_valid_scenarios_dict: dict[str, Any],
) -> None:
    """Verifica que assert_zero_resilience_failures no lance excepción en reportes limpios."""
    report = ResilienceReporter.parse_playwright_results(sample_valid_scenarios_dict)
    ResilienceReporter.assert_zero_resilience_failures(report)


def test_assert_zero_resilience_failures_wsod_raises() -> None:
    """Verifica que la presencia de WSOD lance FrontendResilienceError con mensaje crítico."""
    report = FrontendResilienceReportDTO(
        report_id="resil-fail-wsod",
        total_scenarios=2,
        passed_scenarios=1,
        is_conforming=False,
        scenarios=[
            ResilienceScenarioResultDTO(
                scenario_id="scen-wsod-01",
                description="Caída en Login",
                failure_type="connection_refused",
                error_boundary_triggered=False,
                white_screen_detected=True,
                recovered_successfully=False,
                passed=False,
            ),
            ResilienceScenarioResultDTO(
                scenario_id="scen-ok-02",
                description="Escenario exitoso",
                failure_type="flaky_reconnection",
                error_boundary_triggered=False,
                white_screen_detected=False,
                recovered_successfully=True,
                passed=True,
            ),
        ],
    )

    with pytest.raises(FrontendResilienceError) as exc_info:
        ResilienceReporter.assert_zero_resilience_failures(report)

    error_msg = str(exc_info.value)
    assert "[CRITICAL WSOD]" in error_msg
    assert "scen-wsod-01" in error_msg


def test_assert_zero_resilience_failures_standard_failure_raises() -> None:
    """Verifica que un fallo sin WSOD lance FrontendResilienceError con detalle [FAILED]."""
    report = FrontendResilienceReportDTO(
        report_id="resil-fail-std",
        total_scenarios=1,
        passed_scenarios=0,
        is_conforming=False,
        scenarios=[
            ResilienceScenarioResultDTO(
                scenario_id="scen-fail-01",
                description="Error de contrato",
                failure_type="http_500",
                error_boundary_triggered=True,
                white_screen_detected=False,
                recovered_successfully=False,
                passed=False,
                error_message="El banner no apareció tras 5 segundos",
            ),
        ],
    )

    with pytest.raises(FrontendResilienceError) as exc_info:
        ResilienceReporter.assert_zero_resilience_failures(report)

    error_msg = str(exc_info.value)
    assert "[FAILED]" in error_msg
    assert "El banner no apareció tras 5 segundos" in error_msg


def test_export_and_load_report_json(tmp_path: Path) -> None:
    """Verifica el ciclo de exportación a JSON y carga/validación con Pydantic."""
    report = FrontendResilienceReportDTO(
        report_id="resil-roundtrip",
        total_scenarios=1,
        passed_scenarios=1,
        is_conforming=True,
        scenarios=[
            ResilienceScenarioResultDTO(
                scenario_id="scen-01",
                description="Roundtrip JSON",
                failure_type="flaky_reconnection",
                error_boundary_triggered=False,
                white_screen_detected=False,
                recovered_successfully=True,
                passed=True,
            )
        ],
    )

    target_file = tmp_path / "artifacts" / "resilience-report.json"
    exported_path = ResilienceReporter.export_report_json(report, target_file)

    assert exported_path.exists()
    assert exported_path == target_file.resolve()

    loaded = ResilienceReporter.load_report_json(exported_path)
    assert loaded.report_id == "resil-roundtrip"
    assert loaded.total_scenarios == 1
    assert loaded.is_conforming is True
    assert loaded.scenarios[0].scenario_id == "scen-01"


def test_load_report_json_not_found(tmp_path: Path) -> None:
    """Verifica que cargar un archivo inexistente lance FileNotFoundError."""
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError, match="El archivo de reporte no existe"):
        ResilienceReporter.load_report_json(missing)


def test_cli_main_success_and_failures(tmp_path: Path) -> None:
    """Verifica las rutas de éxito y fallo del CLI interactivo."""
    ok_report = tmp_path / "ok.json"
    ok_report.write_text(
        json.dumps(
            {
                "report_id": "resil-cli-ok",
                "created_at": "2026-11-09T20:00:00Z",
                "total_scenarios": 1,
                "passed_scenarios": 1,
                "is_conforming": True,
                "scenarios": [
                    {
                        "scenario_id": "s-1",
                        "description": "OK",
                        "failure_type": "timeout",
                        "error_boundary_triggered": False,
                        "white_screen_detected": False,
                        "recovered_successfully": True,
                        "passed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. Ejecución informativa (sin check)
    assert main(["--report-file", str(ok_report)]) == 0

    # 2. Ejecución con compuerta estricta exitosa
    assert main(["--report-file", str(ok_report), "--check-failures"]) == 0

    # 3. Ejecución con compuerta estricta fallida
    fail_report = tmp_path / "fail.json"
    fail_report.write_text(
        json.dumps(
            {
                "report_id": "resil-cli-fail",
                "created_at": "2026-11-09T20:00:00Z",
                "total_scenarios": 1,
                "passed_scenarios": 0,
                "is_conforming": False,
                "scenarios": [
                    {
                        "scenario_id": "s-2",
                        "description": "Fail",
                        "failure_type": "http_503",
                        "error_boundary_triggered": True,
                        "white_screen_detected": True,
                        "recovered_successfully": False,
                        "passed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["--report-file", str(fail_report), "--check-failures"]) == 1

    # 4. Archivo inexistente -> código fatal 2
    assert main(["--report-file", str(tmp_path / "no_existe.json")]) == 2
