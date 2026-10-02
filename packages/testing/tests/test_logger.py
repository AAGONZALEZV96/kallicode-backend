"""Pruebas unitarias para el logger estructurado JSON."""

import json

import structlog
from kallicode_testing.logger import get_logger
from pytest import CaptureFixture


def test_logger_json_format_and_traceability_fields(capsys: CaptureFixture[str]) -> None:
    """Verifica que el logger emita un JSON válido con los campos obligatorios."""
    structlog.reset_defaults()

    logger = get_logger(job_id="job-ci-001", tenant_id="tenant-kalli", trace_id="req-999")

    logger.info("Prueba E2E finalizada", duracion_ms=145.2)

    captured = capsys.readouterr()
    log_line = captured.out.strip().split("\n")[-1]

    log_data = json.loads(log_line)

    assert log_data["job_id"] == "job-ci-001"
    assert log_data["tenant_id"] == "tenant-kalli"
    assert log_data["trace_id"] == "req-999"
    assert log_data["duracion_ms"] == 145.2
    assert log_data["event"] == "Prueba E2E finalizada"
    assert log_data["level"] == "info"
    assert "timestamp" in log_data
