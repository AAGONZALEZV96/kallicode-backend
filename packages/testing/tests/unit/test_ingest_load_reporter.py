"""Pruebas unitarias para IngestLoadReporter y DTOs de carga de ingesta (KC-QA-B6-05).

Ticket: KC-QA-B6-05
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.ingest_load_reporter import (
    IngestBatchMetricDTO,
    IngestLoadReporter,
    IngestPerformanceBudgetExceededError,
    IngestPipelineReportDTO,
    main,
)
from pydantic import ValidationError


@pytest.fixture
def sample_ingest_summary_conforming() -> dict[str, Any]:
    """Resumen k6 realista simulando 10 repositorios sintéticos multi-tenant conformes."""
    catalog = [
        {
            "tenant_id": f"tenant-ingest-0{(i // 2) + 1}",
            "repo_name": f"repo-sintetico-{i + 1:02d}",
            "files_count": 20 + i,
            "loc_total": 2500 + (i * 150),
            "chunks_vectorized": 100 + (i * 6),
        }
        for i in range(10)
    ]
    return {
        "state": {"testRunDurationMs": 120000.0},
        "metrics": {
            "ingest_batch_duration": {"values": {"avg": 240.0, "p(95)": 420.5, "count": 40}},
            "ingest_loc_per_second": {"values": {"avg": 12400.0}},
            "ingest_chunks_per_second": {"values": {"avg": 496.0}},
            "ingest_memory_rss_mb": {"values": {"value": 640.0, "min": 420.0, "max": 780.5}},
            "ingest_tenant_leak_count": {"values": {"count": 0}},
            "ingest_http_errors": {"values": {"rate": 0.0}},
        },
        "kallicode_ingest_metadata": {
            "ticket": "KC-QA-B6-05",
            "endpoint": "/api/v1/onboarding/ingest",
            "fast_mode": False,
            "repos_catalog": catalog,
        },
    }


def test_parse_ingest_summary_exitoso(sample_ingest_summary_conforming: dict[str, Any]) -> None:
    """1. Parseo de métricas k6 extrayendo rendimiento de LOC/s, chunks/s y 10 repos."""
    report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)

    assert isinstance(report, IngestPipelineReportDTO)
    assert report.total_repos_processed == 10
    assert report.avg_loc_per_second == 12400.0
    assert report.avg_chunks_per_second == 496.0
    assert report.max_memory_rss_mb == 780.5
    assert report.p95_latency_ms == 420.5
    assert report.zero_tenant_leaks is True
    assert report.is_conforming is True
    assert len(report.batches) == 10


def test_assert_sla_compliance_exito(sample_ingest_summary_conforming: dict[str, Any]) -> None:
    """2. Valida aprobación cuando latencia p95 < 800ms y memoria RSS < 1.5 GB (1500 MB)."""
    report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)
    IngestLoadReporter.assert_ingest_sla_compliance(report)


def test_assert_sla_compliance_falla_memoria(
    sample_ingest_summary_conforming: dict[str, Any],
) -> None:
    """3. Lanza IngestPerformanceBudgetExceededError si la memoria RSS supera 1500 MB."""
    sample_ingest_summary_conforming["metrics"]["ingest_memory_rss_mb"]["values"]["max"] = 1650.25

    report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)
    assert report.max_memory_rss_mb == 1650.25
    assert report.is_conforming is False

    with pytest.raises(IngestPerformanceBudgetExceededError, match="1650.25MB"):
        IngestLoadReporter.assert_ingest_sla_compliance(report)


def test_aislamiento_multi_tenant_ingesta(
    sample_ingest_summary_conforming: dict[str, Any],
) -> None:
    """4. Valida zero_tenant_leaks en el informe consolidado y fallo ante fuga cruzada."""
    clean_report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)
    assert clean_report.zero_tenant_leaks is True

    # Simular fuga detectada entre tenants en k6
    sample_ingest_summary_conforming["metrics"]["ingest_tenant_leak_count"]["values"]["count"] = 2
    leaked_report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)
    assert leaked_report.zero_tenant_leaks is False
    assert leaked_report.is_conforming is False

    with pytest.raises(IngestPerformanceBudgetExceededError, match="zero_tenant_leaks=False"):
        IngestLoadReporter.assert_ingest_sla_compliance(leaked_report)


def test_inmutabilidad_dtos_ingest() -> None:
    """5. Confirma que IngestBatchMetricDTO e IngestPipelineReportDTO tienen frozen=True."""
    batch = IngestBatchMetricDTO(
        tenant_id="tenant-ingest-01",
        repo_name="billing-core-api",
        files_count=24,
        loc_total=3200,
        chunks_vectorized=128,
        duration_seconds=0.35,
        loc_per_second=9142.86,
        memory_peak_mb=512.0,
        passed=True,
    )
    report = IngestPipelineReportDTO(
        total_repos_processed=1,
        avg_loc_per_second=9142.86,
        max_memory_rss_mb=512.0,
        zero_tenant_leaks=True,
        is_conforming=True,
        batches=[batch],
    )

    with pytest.raises(ValidationError):
        batch.passed = False

    with pytest.raises(ValidationError):
        report.is_conforming = False


def test_sla_latencia_p95_bordes_exportacion_y_cli(
    tmp_path: Path,
    sample_ingest_summary_conforming: dict[str, Any],
) -> None:
    """Cubre latencia p95 >= 800ms, lotes directos, anti falso verde, JSON WORM y CLI."""
    # Latencia p95 >= 800ms debe lanzar IngestPerformanceBudgetExceededError
    sample_ingest_summary_conforming["metrics"]["ingest_batch_duration"]["values"]["p(95)"] = 910.0
    slow_report = IngestLoadReporter.parse_k6_summary(sample_ingest_summary_conforming)
    assert slow_report.is_conforming is False
    with pytest.raises(IngestPerformanceBudgetExceededError, match="910.00ms"):
        IngestLoadReporter.assert_ingest_sla_compliance(slow_report)

    # Ruta A: lista de batches pre-agregada (dicts + DTO)
    dto_batch = IngestBatchMetricDTO(
        tenant_id="tenant-b",
        repo_name="repo-b",
        files_count=10,
        loc_total=1000,
        chunks_vectorized=40,
        duration_seconds=0.2,
        loc_per_second=5000.0,
        memory_peak_mb=400.0,
        passed=True,
    )
    batches_payload: dict[str, Any] = {
        "zero_tenant_leaks": True,
        "batches": [
            {
                "tenant_id": "tenant-a",
                "repo_name": "repo-a",
                "files_count": 15,
                "loc_total": 1500,
                "chunks_vectorized": 60,
                "duration_seconds": 0.25,
                "memory_peak_mb": 480.0,
            },
            dto_batch,
        ],
    }
    report_batches = IngestLoadReporter.parse_k6_summary(batches_payload)
    assert report_batches.total_repos_processed == 2
    assert report_batches.max_memory_rss_mb == 480.0
    assert report_batches.is_conforming is True
    IngestLoadReporter.assert_ingest_sla_compliance(report_batches)

    # Fallback global sin kallicode_ingest_metadata
    global_k6: dict[str, Any] = {
        "metrics": {
            "http_req_duration": {"values": {"avg": 150.0, "p(95)": 250.0, "count": 5}},
            "ingest_loc_total": {"values": {"count": 7500}},
            "ingest_chunks_total": {"values": {"count": 300}},
            "ingest_memory_rss_mb": {"values": {"max": 320.0}},
        }
    }
    r_global = IngestLoadReporter.parse_k6_summary(global_k6)
    assert r_global.total_repos_processed == 1
    assert r_global.is_conforming is True

    # Anti falso verde con resumen vacío
    empty_report = IngestLoadReporter.parse_k6_summary({"metrics": {}})
    assert empty_report.is_conforming is False
    with pytest.raises(IngestPerformanceBudgetExceededError, match="no procesó repositorios"):
        IngestLoadReporter.assert_ingest_sla_compliance(empty_report)

    # Exportación WORM y CLI
    exported = IngestLoadReporter.export_report_json(
        report_batches,
        tmp_path / "worm" / "ingest-pipeline-report.json",
    )
    assert exported.exists()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["report_id"] == report_batches.report_id

    summary_file = tmp_path / "ingest-summary.json"
    cli_payload = {
        "zero_tenant_leaks": True,
        "batches": [batches_payload["batches"][0], dto_batch.model_dump()],
    }
    summary_file.write_text(json.dumps(cli_payload), encoding="utf-8")
    cli_out = tmp_path / "cli-ingest-report.json"
    assert (
        main(
            [
                "--summary-file",
                str(summary_file),
                "--output-file",
                str(cli_out),
                "--check-sla",
            ]
        )
        == 0
    )
    assert cli_out.exists()

    # CLI exit 1 ante violación y ante archivo faltante
    cli_payload["batches"][0]["memory_peak_mb"] = 1800.0
    summary_file.write_text(json.dumps(cli_payload), encoding="utf-8")
    assert main(["--summary-file", str(summary_file), "--check-sla"]) == 1
    assert main(["--summary-file", str(tmp_path / "no_existe.json")]) == 1

    # Validaciones de tipo
    with pytest.raises(TypeError):
        IngestLoadReporter.parse_k6_summary("invalid")

    with pytest.raises(TypeError):
        IngestLoadReporter.parse_k6_summary({"batches": [123]})

    manual_fail = IngestPipelineReportDTO(
        total_repos_processed=1,
        avg_loc_per_second=1000.0,
        max_memory_rss_mb=300.0,
        zero_tenant_leaks=True,
        is_conforming=False,
        p95_latency_ms=100.0,
    )
    with pytest.raises(IngestPerformanceBudgetExceededError, match="is_conforming=False"):
        IngestLoadReporter.assert_ingest_sla_compliance(manual_fail)
