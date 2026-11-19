"""Pruebas unitarias para LoadReporter y DTOs de carga k6 (KC-QA-B6-01).

Ticket: KC-QA-B6-01
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Decisión Arquitectónica: D16
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.load_reporter import (
    K6MetricSummaryDTO,
    LoadReporter,
    LoadSlaExceededError,
    LoadTestReportDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_k6_summary_conforming() -> dict[str, Any]:
    """Fixture realista de `k6-summary.json` cumpliendo SLAs y D16."""
    return {
        "state": {"testRunDurationMs": 360000.0},
        "metrics": {
            "vus_max": {"values": {"max": 100, "min": 1, "value": 100}},
            "http_req_duration{endpoint:kanban_board}": {
                "values": {
                    "avg": 65.4,
                    "p(90)": 112.0,
                    "p(95)": 145.8,
                    "p(99)": 280.4,
                }
            },
            "http_req_duration{endpoint:ticket_detail}": {
                "values": {
                    "avg": 58.1,
                    "p(90)": 98.0,
                    "p(95)": 132.5,
                    "p(99)": 240.0,
                }
            },
            "http_req_duration{endpoint:gate_sign}": {
                "values": {
                    "avg": 92.3,
                    "p(90)": 140.0,
                    "p(95)": 188.0,
                    "p(99)": 350.0,
                }
            },
            "http_5xx_errors": {"values": {"rate": 0.0}},
            "http_5xx_errors{endpoint:kanban_board}": {"values": {"rate": 0.0}},
            "http_5xx_errors{endpoint:ticket_detail}": {"values": {"rate": 0.0}},
            "http_5xx_errors{endpoint:gate_sign}": {"values": {"rate": 0.0}},
            "d16_rate_limit_verified": {"values": {"rate": 1.0, "passes": 1, "fails": 0}},
            "d16_429_hits": {"values": {"count": 1}},
            "d16_retry_after_present": {"values": {"count": 1}},
        },
        "kallicode_metadata": {
            "ticket": "KC-QA-B6-01",
            "decision": "D16",
            "rate_limit_per_min": 120,
            "rate_limit_verified": True,
            "endpoints": [
                {
                    "key": "kanban_board",
                    "endpoint": "/api/v1/tickets?vista=kanban",
                    "method": "GET",
                },
                {
                    "key": "ticket_detail",
                    "endpoint": "/api/v1/tickets/KC-1001",
                    "method": "GET",
                },
                {
                    "key": "gate_sign",
                    "endpoint": "/api/v1/tickets/KC-1001/gates/1/firma",
                    "method": "POST",
                },
            ],
        },
    }


def test_parse_k6_summary_exitoso(sample_k6_summary_conforming: dict[str, Any]) -> None:
    """1. Parseo de métricas k6 con cumplimiento total de SLAs."""
    report = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)

    assert isinstance(report, LoadTestReportDTO)
    assert report.vus_max == 100
    assert report.duration_seconds == 360.0
    assert report.is_conforming is True
    assert report.rate_limit_verified is True
    assert len(report.metrics) == 3

    kanban_metric = next(m for m in report.metrics if m.endpoint == "/api/v1/tickets?vista=kanban")
    assert kanban_metric.method == "GET"
    assert kanban_metric.p90_ms == 112.0
    assert kanban_metric.p95_ms == 145.8
    assert kanban_metric.p99_ms == 280.4
    assert kanban_metric.error_rate == 0.0
    assert kanban_metric.passed_sla is True

    # No debe lanzar excepción al afirmar cero violaciones
    LoadReporter.assert_zero_sla_violations(report)


def test_assert_zero_sla_violations_lanza_excepcion(
    sample_k6_summary_conforming: dict[str, Any],
) -> None:
    """2. Valida lanzamiento de LoadSlaExceededError ante p95 mayor a 200ms en Kanban."""
    sample_k6_summary_conforming["metrics"]["http_req_duration{endpoint:kanban_board}"]["values"][
        "p(95)"
    ] = 245.5

    report = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)
    assert report.is_conforming is False

    kanban_metric = next(m for m in report.metrics if m.endpoint == "/api/v1/tickets?vista=kanban")
    assert kanban_metric.passed_sla is False

    with pytest.raises(LoadSlaExceededError, match="p95 Kanban=245.50ms"):
        LoadReporter.assert_zero_sla_violations(report)


def test_verificacion_rate_limit_d16(sample_k6_summary_conforming: dict[str, Any]) -> None:
    """3. Confirma que se reporte verificación del límite de 120 req/min (Decisión D16)."""
    report_verified = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)
    assert report_verified.rate_limit_verified is True

    # Simular resumen sin metadata explícita, deduciendo desde métricas nativas D16
    only_metrics_summary: dict[str, Any] = {
        "state": {"testRunDurationMs": 60000.0},
        "metrics": {
            "vus_max": {"values": {"max": 10}},
            "http_req_duration{endpoint:kanban_board}": {
                "values": {"p(90)": 40.0, "p(95)": 55.0, "p(99)": 90.0}
            },
            "d16_rate_limit_verified": {"values": {"rate": 1.0, "passes": 1, "fails": 0}},
        },
    }
    report_from_metric = LoadReporter.parse_k6_summary(only_metrics_summary)
    assert report_from_metric.rate_limit_verified is True

    # Simular falla en verificación D16 (ej. petición 121 no devolvió 429 + Retry-After)
    failed_d16_summary: dict[str, Any] = {
        "state": {"testRunDurationMs": 60000.0},
        "metrics": {
            "vus_max": {"values": {"max": 10}},
            "http_req_duration{endpoint:kanban_board}": {
                "values": {"p(90)": 40.0, "p(95)": 55.0, "p(99)": 90.0}
            },
            "d16_rate_limit_verified": {"values": {"rate": 0.0, "passes": 0, "fails": 1}},
        },
    }
    report_unverified = LoadReporter.parse_k6_summary(failed_d16_summary)
    assert report_unverified.rate_limit_verified is False


def test_inmutabilidad_dtos_carga() -> None:
    """4. Confirma que los modelos Pydantic K6MetricSummaryDTO y LoadTestReportDTO tengan frozen=True."""
    metric = K6MetricSummaryDTO(
        endpoint="/api/v1/tickets?vista=kanban",
        method="GET",
        p90_ms=80.0,
        p95_ms=120.0,
        p99_ms=210.0,
        error_rate=0.0,
        passed_sla=True,
    )
    report = LoadTestReportDTO(
        vus_max=100,
        duration_seconds=360.0,
        metrics=[metric],
        rate_limit_verified=True,
        is_conforming=True,
    )

    with pytest.raises(ValidationError):
        metric.p95_ms = 999.0

    with pytest.raises(ValidationError):
        report.is_conforming = False


def test_assert_sla_rechaza_p99_y_5xx_y_vacio(
    sample_k6_summary_conforming: dict[str, Any],
) -> None:
    """Prohibición de falsos verdes: rechaza p99 >= 500ms, error 5xx > 0 y resumen vacío."""
    # p99 >= 500ms en gate_sign
    sample_k6_summary_conforming["metrics"]["http_req_duration{endpoint:gate_sign}"]["values"][
        "p(99)"
    ] = 525.0
    report_p99 = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)
    assert report_p99.is_conforming is False
    with pytest.raises(LoadSlaExceededError, match="p99=525.00ms"):
        LoadReporter.assert_zero_sla_violations(report_p99)

    # Error 5xx > 0.0
    sample_k6_summary_conforming["metrics"]["http_req_duration{endpoint:gate_sign}"]["values"][
        "p(99)"
    ] = 250.0
    sample_k6_summary_conforming["metrics"]["http_5xx_errors{endpoint:gate_sign}"]["values"][
        "rate"
    ] = 0.02
    report_5xx = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)
    assert report_5xx.is_conforming is False
    with pytest.raises(LoadSlaExceededError, match="error_rate 5xx=0.0200"):
        LoadReporter.assert_zero_sla_violations(report_5xx)

    # Resumen sin métricas (prohibido iterar al vacío y dar verde)
    empty_report = LoadReporter.parse_k6_summary({"metrics": {}})
    assert empty_report.is_conforming is False
    with pytest.raises(LoadSlaExceededError, match="no contiene métricas"):
        LoadReporter.assert_zero_sla_violations(empty_report)


def test_export_report_json_y_cli(
    tmp_path: Path,
    sample_k6_summary_conforming: dict[str, Any],
) -> None:
    """Verifica exportación determinista JSON para WORM y funcionamiento CLI."""
    report = LoadReporter.parse_k6_summary(sample_k6_summary_conforming)
    exported_path = LoadReporter.export_report_json(
        report,
        tmp_path / "worm" / "load_report.json",
    )

    assert exported_path.exists()
    loaded_payload = json.loads(exported_path.read_text(encoding="utf-8"))
    assert loaded_payload["report_id"] == report.report_id
    assert loaded_payload["vus_max"] == 100
    assert loaded_payload["rate_limit_verified"] is True
    assert len(loaded_payload["metrics"]) == 3

    # CLIexit 0 en escenario conforme
    summary_file = tmp_path / "k6-summary.json"
    summary_file.write_text(json.dumps(sample_k6_summary_conforming), encoding="utf-8")
    cli_output = tmp_path / "cli-report.json"

    exit_ok = main(
        [
            "--summary-file",
            str(summary_file),
            "--output-file",
            str(cli_output),
            "--check-sla",
        ]
    )
    assert exit_ok == 0
    assert cli_output.exists()

    # CLI exit 1 en escenario con violación de SLA
    sample_k6_summary_conforming["metrics"]["http_req_duration{endpoint:kanban_board}"]["values"][
        "p(95)"
    ] = 310.0
    summary_file.write_text(json.dumps(sample_k6_summary_conforming), encoding="utf-8")
    exit_fail = main(["--summary-file", str(summary_file), "--check-sla"])
    assert exit_fail == 1

    # CLI exit 1 cuando no existe archivo
    assert main(["--summary-file", str(tmp_path / "missing.json")]) == 1


def test_parse_k6_summary_fallbacks_y_errores() -> None:
    """Cubre fallbacks de métricas globales, custom trends, listas de DTOs y validaciones."""
    # Fallback a http_req_duration global y contadores D16
    global_summary: dict[str, Any] = {
        "duration_seconds": 15.0,
        "vus_max": 5,
        "metrics": {
            "http_req_duration": {"values": {"p(90)": 40.0, "p(95)": 80.0, "p(99)": 120.0}},
            "http_5xx_errors": {"values": {"value": 0.0}},
            "d16_429_hits": {"values": {"count": 1}},
            "d16_retry_after_present": {"values": {"count": 1}},
        },
    }
    r_global = LoadReporter.parse_k6_summary(global_summary)
    assert r_global.is_conforming is True
    assert r_global.rate_limit_verified is True
    assert r_global.duration_seconds == 15.0
    assert r_global.vus_max == 5

    # Custom Trend metrics (http_req_duration_<key>) y lista pre-agregada
    custom_summary: dict[str, Any] = {
        "metrics": {
            "http_req_duration_kanban_board": {"p(90)": 50.0, "p(95)": 70.0, "p(99)": 100.0},
            "http_5xx_errors_kanban_board": {"rate": 0.0},
            "d16_rate_limit_verified": {"values": {"rate": 1.0}},
        }
    }
    r_custom = LoadReporter.parse_k6_summary(custom_summary)
    assert r_custom.is_conforming is True
    assert r_custom.rate_limit_verified is True

    # Lista de dicts y DTOs directos
    list_summary: dict[str, Any] = {
        "vus_max": 10,
        "duration_seconds": 10.0,
        "rate_limit_verified": True,
        "metrics": [
            {
                "endpoint": "/api/v1/tickets?vista=kanban",
                "method": "GET",
                "p90_ms": 50.0,
                "p95_ms": 90.0,
                "p99_ms": 140.0,
                "error_rate": 0.0,
            },
            K6MetricSummaryDTO(
                endpoint="/api/v1/tickets/KC-1001",
                method="GET",
                p90_ms=45.0,
                p95_ms=80.0,
                p99_ms=110.0,
                error_rate=0.0,
                passed_sla=True,
            ),
        ],
    }
    r_list = LoadReporter.parse_k6_summary(list_summary)
    assert r_list.is_conforming is True
    assert len(r_list.metrics) == 2

    # Tipos inválidos
    with pytest.raises(TypeError):
        LoadReporter.parse_k6_summary("invalid")

    with pytest.raises(TypeError):
        LoadReporter.parse_k6_summary({"metrics": [123]})

    # Reporte marcado como no conforme manualmente pese a métricas válidas
    manual_non_conforming = LoadTestReportDTO(
        vus_max=10,
        duration_seconds=10.0,
        metrics=[r_list.metrics[0]],
        rate_limit_verified=True,
        is_conforming=False,
    )
    with pytest.raises(LoadSlaExceededError, match="is_conforming=False"):
        LoadReporter.assert_zero_sla_violations(manual_non_conforming)
