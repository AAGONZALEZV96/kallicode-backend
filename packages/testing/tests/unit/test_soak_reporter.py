"""Pruebas unitarias para el analizador de Soak Testing y Fugas de Memoria (KC-QA-B6-09)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from kallicode_testing.soak_reporter import (
    MemoryLeakDetectedError,
    MemorySampleDTO,
    SoakReporter,
    SoakTestReportDTO,
    main,
)
from pydantic import ValidationError


def _flat_memory_summary() -> dict[str, Any]:
    """Construye un `soak-summary.json` de 4 horas con RAM plana tras warm-up y 0 errores."""
    return {
        "kallicode_soak_metadata": {
            "ticket": "KC-QA-B6-09",
            "fast_mode": False,
            "total_duration_seconds": 14400.0,
            "warmup_seconds": 1800.0,
        },
        "metrics": {
            "http_req_duration": {"values": {"p(95)": 142.5, "p(99)": 210.0}},
            "soak_warmup_duration": {"values": {"p(95)": 138.0}},
            "soak_final_duration": {"values": {"p(95)": 145.0}},
            "http_5xx_errors": {"values": {"rate": 0.0}},
            "soak_oom_killed_count": {"values": {"count": 0}},
        },
        "memory_samples": [
            {"timestamp_seconds": 60.0, "memory_rss_mb": 210.0, "active_vus": 15},
            {"timestamp_seconds": 1800.0, "memory_rss_mb": 240.0, "active_vus": 15},
            {"timestamp_seconds": 5400.0, "memory_rss_mb": 242.5, "active_vus": 15},
            {"timestamp_seconds": 9000.0, "memory_rss_mb": 244.0, "active_vus": 15},
            {"timestamp_seconds": 12600.0, "memory_rss_mb": 245.5, "active_vus": 15},
            {"timestamp_seconds": 14400.0, "memory_rss_mb": 246.0, "active_vus": 15},
        ],
    }


def test_soak_evaluation_plana_sin_fuga(tmp_path: Path) -> None:
    """Valida aprobación cuando delta_rss < 10%, pendiente cercana a 0 y 0 errores 5xx/OOM."""
    reporter = SoakReporter()
    report = reporter.parse_k6_summary(_flat_memory_summary())

    reporter.assert_no_memory_leak(report)

    assert report.duration_seconds == 14400.0
    assert report.initial_rss_mb == 240.0  # post warm-up (t=1800s)
    assert report.final_rss_mb == 246.0
    assert report.delta_rss_pct == 2.5
    assert abs(report.memory_slope_mb_per_hour) < 2.5
    assert report.p95_latency_ms == 142.5
    assert report.is_leak_detected is False
    assert report.is_conforming is True

    exported = reporter.export_report_json(report, tmp_path / "worm" / "soak-report.json")
    assert exported.exists()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["report_id"] == report.report_id
    assert data["is_conforming"] is True


def test_assert_no_memory_leak_detecta_fuga_creciente() -> None:
    """Lanza MemoryLeakDetectedError si delta_rss >= 10% o pendiente refleja fuga lineal."""
    reporter = SoakReporter()
    leaking_summary = _flat_memory_summary()
    leaking_summary["memory_samples"] = [
        {"timestamp_seconds": 1800.0, "memory_rss_mb": 240.0, "active_vus": 15},
        {"timestamp_seconds": 5400.0, "memory_rss_mb": 290.0, "active_vus": 15},
        {"timestamp_seconds": 9000.0, "memory_rss_mb": 340.0, "active_vus": 15},
        {"timestamp_seconds": 14400.0, "memory_rss_mb": 405.0, "active_vus": 15},
    ]

    report = reporter.parse_k6_summary(leaking_summary)
    assert report.delta_rss_pct > 10.0
    assert report.memory_slope_mb_per_hour > 20.0
    assert report.is_leak_detected is True
    assert report.is_conforming is False

    with pytest.raises(MemoryLeakDetectedError, match="Crecimiento de memoria RSS"):
        reporter.assert_no_memory_leak(report)

    # También debe bloquear cuando delta es 6% (<10%) pero la pendiente por hora es alta
    slope_summary = _flat_memory_summary()
    slope_summary["kallicode_soak_metadata"]["warmup_seconds"] = 0.0
    slope_summary["kallicode_soak_metadata"]["total_duration_seconds"] = 1800.0
    slope_summary["memory_samples"] = [
        {"timestamp_seconds": 0.0, "memory_rss_mb": 200.0, "active_vus": 10},
        {"timestamp_seconds": 900.0, "memory_rss_mb": 206.0, "active_vus": 10},
        {"timestamp_seconds": 1800.0, "memory_rss_mb": 212.0, "active_vus": 10},
    ]
    slope_report = reporter.parse_k6_summary(slope_summary)
    assert slope_report.delta_rss_pct == 6.0
    assert slope_report.memory_slope_mb_per_hour == 24.0
    assert slope_report.is_leak_detected is True
    with pytest.raises(MemoryLeakDetectedError, match="Pendiente positiva"):
        reporter.assert_no_memory_leak(slope_report)


def test_parse_soak_summary_extrae_muestras_y_metricas() -> None:
    """Comprueba extracción adecuada de percentiles, degradación, 5xx, OOM y pendiente OLS."""
    reporter = SoakReporter()
    summary = _flat_memory_summary()
    summary["metrics"]["soak_final_duration"] = {"values": {"p(95)": 190.0}}

    report = reporter.parse_k6_summary(summary)

    assert report.p95_latency_ms == 142.5
    # 138ms -> 190ms = +37.68% (> 15%)
    assert report.latency_degradation_pct > 15.0
    assert report.is_conforming is False
    with pytest.raises(MemoryLeakDetectedError, match="Degradación de latencia p95"):
        reporter.assert_no_memory_leak(report)

    # OOMKilled o errores 5xx también deben bloquear
    oom_summary = _flat_memory_summary()
    oom_summary["metrics"]["soak_oom_killed_count"] = {"values": {"count": 1}}
    oom_summary["metrics"]["http_5xx_errors"] = {"values": {"rate": 0.02}}
    oom_report = reporter.parse_k6_summary(oom_summary)
    with pytest.raises(MemoryLeakDetectedError) as exc_info:
        reporter.assert_no_memory_leak(oom_report)
    assert "OOMKilled" in str(exc_info.value)
    assert "HTTP 5xx" in str(exc_info.value)


def test_prevencion_falsos_verdes_sin_muestras() -> None:
    """Rechaza reportes vacíos, sin muestras RSS o con duración en cero."""
    reporter = SoakReporter()

    empty_report = reporter.parse_k6_summary({"metrics": {}})
    assert empty_report.duration_seconds == 0.0
    assert empty_report.is_conforming is False
    with pytest.raises(MemoryLeakDetectedError, match="Duración de ejecución inválida"):
        reporter.assert_no_memory_leak(empty_report)

    no_samples_summary = _flat_memory_summary()
    no_samples_summary["memory_samples"] = []
    no_samples_report = reporter.parse_k6_summary(no_samples_summary)
    assert no_samples_report.is_conforming is False
    with pytest.raises(MemoryLeakDetectedError, match="sin serie mínima de muestras"):
        reporter.assert_no_memory_leak(no_samples_report)

    assert reporter.calculate_memory_slope([]) == 0.0
    with pytest.raises(TypeError):
        reporter.parse_k6_summary(cast(Any, "invalid"))


def test_inmutabilidad_dtos_soak() -> None:
    """Valida ConfigDict(frozen=True) y __test__ = False en DTOs Pydantic v2."""
    for dto_cls in (MemorySampleDTO, SoakTestReportDTO):
        assert dto_cls.__test__ is False

    reporter = SoakReporter()
    report = reporter.parse_k6_summary(_flat_memory_summary())

    with pytest.raises(ValidationError):
        report.is_conforming = False
    with pytest.raises(ValidationError):
        report.delta_rss_pct = 99.9
    with pytest.raises(ValidationError):
        report.samples[0].memory_rss_mb = 9999.0


def test_soak_cli_check_exit_codes(tmp_path: Path) -> None:
    """Valida flujo CLI de SoakReporter con salida explícita (exit 0 y exit 1)."""
    summary_file = tmp_path / "soak-summary.json"
    out_file = tmp_path / "soak-report.json"

    summary_file.write_text(json.dumps(_flat_memory_summary()), encoding="utf-8")
    assert (
        main(
            [
                "--summary-file",
                str(summary_file),
                "--output-file",
                str(out_file),
                "--check-soak",
            ]
        )
        == 0
    )
    assert out_file.exists()

    leaking = _flat_memory_summary()
    leaking["memory_samples"][-1]["memory_rss_mb"] = 500.0
    summary_file.write_text(json.dumps(leaking), encoding="utf-8")
    assert (
        main(
            [
                "--summary-file",
                str(summary_file),
                "--output-file",
                str(out_file),
                "--check-soak",
            ]
        )
        == 1
    )

    assert main(["--summary-file", str(tmp_path / "no_existe.json")]) == 1
