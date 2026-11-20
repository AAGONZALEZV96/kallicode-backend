"""Pruebas unitarias para StressReporter y DTOs de estrés k6 (KC-QA-B6-02).

Ticket: KC-QA-B6-02
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.stress_reporter import (
    StressRecoveryFailedError,
    StressReporter,
    StressStageMetricDTO,
    StressTestReportDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_stress_summary_with_break_and_recovery() -> dict[str, Any]:
    """Resumen k6 realista: estable hasta 100 VUs, ruptura en 200 VUs y recuperación en cooldown."""
    return {
        "state": {"testRunDurationMs": 480000.0},
        "metrics": {
            "stress_stage_20_vus_duration": {"values": {"p(95)": 45.0, "p(99)": 80.0}},
            "stress_stage_20_vus_errors": {"values": {"rate": 0.0}},
            "stress_stage_20_vus_reqs": {"values": {"count": 2400}},
            "stress_stage_50_vus_duration": {"values": {"p(95)": 95.0, "p(99)": 180.0}},
            "stress_stage_50_vus_errors": {"values": {"rate": 0.0}},
            "stress_stage_50_vus_reqs": {"values": {"count": 6000}},
            "stress_stage_100_vus_duration": {"values": {"p(95)": 175.0, "p(99)": 350.0}},
            "stress_stage_100_vus_errors": {"values": {"rate": 0.005}},
            "stress_stage_100_vus_reqs": {"values": {"count": 24000}},
            "stress_stage_200_vus_duration": {"values": {"p(95)": 1350.0, "p(99)": 2400.0}},
            "stress_stage_200_vus_errors": {"values": {"rate": 0.14}},
            "stress_stage_200_vus_reqs": {"values": {"count": 18000}},
            "stress_stage_300_vus_duration": {"values": {"p(95)": 2900.0, "p(99)": 4500.0}},
            "stress_stage_300_vus_errors": {"values": {"rate": 0.42}},
            "stress_stage_300_vus_reqs": {"values": {"count": 9000}},
            "stress_cooldown_duration": {"values": {"p(95)": 85.0, "p(99)": 140.0}},
            "stress_cooldown_errors": {"values": {"rate": 0.0}},
            "stress_cooldown_reqs": {"values": {"count": 1200}},
            "recovery_req_duration": {"values": {"p(95)": 62.5, "p(99)": 110.0, "count": 10}},
            "recovery_error_rate": {"values": {"rate": 0.0}},
        },
        "kallicode_stress_metadata": {
            "ticket": "KC-QA-B6-02",
            "fast_mode": False,
            "total_duration_seconds": 480.0,
            "stages": [
                {"name": "stage_20_vus", "vus_target": 20, "duration_seconds": 60.0},
                {"name": "stage_50_vus", "vus_target": 50, "duration_seconds": 60.0},
                {"name": "stage_100_vus", "vus_target": 100, "duration_seconds": 120.0},
                {"name": "stage_200_vus", "vus_target": 200, "duration_seconds": 120.0},
                {"name": "stage_300_vus", "vus_target": 300, "duration_seconds": 60.0},
                {"name": "cooldown", "vus_target": 0, "duration_seconds": 60.0},
            ],
        },
    }


def test_parse_stress_summary_exitoso(
    sample_stress_summary_with_break_and_recovery: dict[str, Any],
) -> None:
    """1. Parseo de métricas k6 extrayendo max_stable_rps y etapas evaluadas."""
    report = StressReporter.parse_k6_summary(sample_stress_summary_with_break_and_recovery)

    assert isinstance(report, StressTestReportDTO)
    assert len(report.stages) == 6
    assert report.max_stable_rps == 200.0  # 24000 reqs / 120s en stage_100_vus
    assert report.recovered_successfully is True
    assert report.is_conforming is True

    stage_100 = next(s for s in report.stages if s.stage_name == "stage_100_vus")
    assert stage_100.vus_target == 100
    assert stage_100.rps_achieved == 200.0
    assert stage_100.stage_passed is True


def test_identificacion_punto_de_ruptura(
    sample_stress_summary_with_break_and_recovery: dict[str, Any],
) -> None:
    """2. Confirma que se detecte la etapa donde la tasa de error o latencia se dispara."""
    report = StressReporter.parse_k6_summary(sample_stress_summary_with_break_and_recovery)

    assert report.breaking_point_vus == 200
    stage_200 = next(s for s in report.stages if s.stage_name == "stage_200_vus")
    assert stage_200.stage_passed is False
    assert stage_200.error_rate == 0.14
    assert stage_200.p95_ms == 1350.0


def test_assert_system_recovered_exito(
    sample_stress_summary_with_break_and_recovery: dict[str, Any],
) -> None:
    """3. Valida aprobación cuando la fase cooldown vuelve a métricas estables (< 300ms p95)."""
    report = StressReporter.parse_k6_summary(sample_stress_summary_with_break_and_recovery)

    assert report.recovered_successfully is True
    StressReporter.assert_system_recovered(report)


def test_assert_system_recovered_falla(
    sample_stress_summary_with_break_and_recovery: dict[str, Any],
) -> None:
    """4. Lanza StressRecoveryFailedError si la API permanece degradada tras el estrés."""
    sample_stress_summary_with_break_and_recovery["metrics"]["recovery_req_duration"]["values"][
        "p(95)"
    ] = 640.0
    sample_stress_summary_with_break_and_recovery["metrics"]["recovery_error_rate"]["values"][
        "rate"
    ] = 0.25
    sample_stress_summary_with_break_and_recovery["metrics"]["stress_cooldown_duration"]["values"][
        "p(95)"
    ] = 520.0

    report = StressReporter.parse_k6_summary(sample_stress_summary_with_break_and_recovery)

    assert report.recovered_successfully is False
    assert report.is_conforming is False

    with pytest.raises(StressRecoveryFailedError, match="cooldown"):
        StressReporter.assert_system_recovered(report)


def test_inmutabilidad_dtos_estres() -> None:
    """5. Confirma que los DTOs StressStageMetricDTO y StressTestReportDTO tengan frozen=True."""
    stage = StressStageMetricDTO(
        stage_name="stage_50_vus",
        vus_target=50,
        rps_achieved=120.5,
        p95_ms=80.0,
        p99_ms=140.0,
        error_rate=0.0,
        stage_passed=True,
    )
    report = StressTestReportDTO(
        max_stable_rps=120.5,
        breaking_point_vus=None,
        recovered_successfully=True,
        stages=[stage],
        is_conforming=True,
    )

    with pytest.raises(ValidationError):
        stage.rps_achieved = 999.0

    with pytest.raises(ValidationError):
        report.recovered_successfully = False


def test_sin_ruptura_y_anti_falso_verde(tmp_path: Path) -> None:
    """Valida escenario sin ruptura hasta 300 VUs, bloqueo de reporte vacío, exportación y CLI."""
    # Escenario donde el backend soporta hasta 300 VUs sin ruptura y recupera en cooldown
    structured_summary: dict[str, Any] = {
        "recovered_successfully": True,
        "stages": [
            {
                "stage_name": "stage_100_vus",
                "vus_target": 100,
                "rps_achieved": 310.0,
                "p95_ms": 140.0,
                "p99_ms": 280.0,
                "error_rate": 0.0,
            },
            {
                "stage_name": "stage_300_vus",
                "vus_target": 300,
                "rps_achieved": 520.0,
                "p95_ms": 250.0,
                "p99_ms": 480.0,
                "error_rate": 0.01,
            },
            {
                "stage_name": "cooldown",
                "vus_target": 0,
                "rps_achieved": 15.0,
                "p95_ms": 42.0,
                "p99_ms": 75.0,
                "error_rate": 0.0,
            },
        ],
    }

    report = StressReporter.parse_k6_summary(structured_summary)
    assert report.breaking_point_vus is None
    assert report.max_stable_rps == 520.0
    assert report.is_conforming is True
    StressReporter.assert_system_recovered(report)

    # Exportación JSON para sellado WORM
    exported = StressReporter.export_report_json(
        report,
        tmp_path / "worm" / "stress-break-report.json",
    )
    assert exported.exists()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["report_id"] == report.report_id
    assert data["max_stable_rps"] == 520.0

    # CLI exit 0 en caso recuperado
    summary_file = tmp_path / "stress-summary.json"
    summary_file.write_text(json.dumps(structured_summary), encoding="utf-8")
    cli_out = tmp_path / "out-report.json"
    assert (
        main(
            [
                "--summary-file",
                str(summary_file),
                "--output-file",
                str(cli_out),
                "--check-recovery",
            ]
        )
        == 0
    )
    assert cli_out.exists()

    # CLI exit 1 si cooldown falló (p95 >= 300ms)
    structured_summary["stages"][2]["p95_ms"] = 450.0
    summary_file.write_text(json.dumps(structured_summary), encoding="utf-8")
    assert (
        main(
            [
                "--summary-file",
                str(summary_file),
                "--output-file",
                str(cli_out),
                "--check-recovery",
            ]
        )
        == 1
    )

    # CLI exit 1 si archivo no existe
    assert main(["--summary-file", str(tmp_path / "no_existe.json")]) == 1

    # Anti falso verde: resumen vacío
    empty_report = StressReporter.parse_k6_summary({"metrics": {}})
    assert empty_report.is_conforming is False
    with pytest.raises(StressRecoveryFailedError, match="no contiene etapas"):
        StressReporter.assert_system_recovered(empty_report)


def test_parse_stress_summary_fallbacks_y_tipos() -> None:
    """Cubre fallback de métricas globales k6 (--vus/--duration), DTOs directos y TypeError."""
    global_k6: dict[str, Any] = {
        "state": {"testRunDurationMs": 10200.0},
        "metrics": {
            "vus_max": {"values": {"max": 5}},
            "http_req_duration": {"values": {"p(95)": 18.0, "p(99)": 35.0}},
            "http_reqs": {"values": {"rate": 42.5, "count": 430}},
            "http_req_failed": {"values": {"rate": 0.0}},
            "recovery_req_duration": {"values": {"p(95)": 12.0, "p(99)": 20.0, "count": 10}},
            "recovery_error_rate": {"values": {"rate": 0.0}},
            "recovery_reqs_count": {"values": {"count": 10}},
        },
    }
    report = StressReporter.parse_k6_summary(global_k6)
    assert report.is_conforming is True
    assert report.recovered_successfully is True
    assert report.max_stable_rps == 42.5
    assert any(s.stage_name == "cooldown_recovery" for s in report.stages)
    StressReporter.assert_system_recovered(report)

    # Lista con StressStageMetricDTO directos + flag booleano explícito
    dto_stage = StressStageMetricDTO(
        stage_name="stage_20_vus",
        vus_target=20,
        rps_achieved=100.0,
        p95_ms=50.0,
        p99_ms=90.0,
        error_rate=0.0,
        stage_passed=True,
    )
    report_dto = StressReporter.parse_k6_summary(
        {"stages": [dto_stage], "recovered_successfully": True}
    )
    assert report_dto.is_conforming is True
    assert report_dto.max_stable_rps == 100.0

    # Reporte marcado manualmente como no recuperado pese a etapas válidas
    manual_fail = StressTestReportDTO(
        max_stable_rps=100.0,
        breaking_point_vus=None,
        recovered_successfully=False,
        stages=[dto_stage],
        is_conforming=False,
    )
    with pytest.raises(StressRecoveryFailedError, match="recovered_successfully=False"):
        StressReporter.assert_system_recovered(manual_fail)

    with pytest.raises(TypeError):
        StressReporter.parse_k6_summary("bad")

    with pytest.raises(TypeError):
        StressReporter.parse_k6_summary({"stages": [123]})
