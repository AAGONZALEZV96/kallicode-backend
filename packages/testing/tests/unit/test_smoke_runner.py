"""Pruebas unitarias para SmokeRunner, DTOs y CLI smoke_test_prod.py (KC-QA-B6-06).

Ticket: KC-QA-B6-06
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from kallicode_testing.smoke_runner import (
    SmokeCheckResultDTO,
    SmokeReportDTO,
    SmokeRunner,
    SmokeTestFailedError,
)
from pydantic import ValidationError

_PROMETHEUS_SAMPLE = """# HELP http_requests_total Total de peticiones HTTP
# TYPE http_requests_total counter
http_requests_total{method="GET",status="200"} 142
# HELP process_resident_memory_bytes Memoria residente
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes 104857600
"""


def _load_smoke_cli_module() -> Any:
    script_path = Path(__file__).resolve().parents[4] / "scripts" / "smoke_test_prod.py"
    spec = importlib.util.spec_from_file_location("smoke_test_prod_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@respx.mock
def test_smoke_runner_todos_los_checks_exitosos() -> None:
    """1. Simula HTTP 200 válidos en los 4 checks; valida all_passed=True y duración < 15s."""
    base = "http://localhost:8000"
    respx.get(f"{base}/health").mock(return_value=httpx.Response(200, json={"estado": "ok"}))
    respx.get(f"{base}/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": True, "redis": True})
    )
    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text=_PROMETHEUS_SAMPLE))
    respx.get(f"{base}/api/v1/auth/health").mock(
        return_value=httpx.Response(200, json={"auth": "ok"})
    )

    runner = SmokeRunner(request_timeout_seconds=3.0, max_retries=2, retry_backoff_seconds=0.0)
    report = runner.run_all_checks(base, timeout_seconds=15.0)

    assert isinstance(report, SmokeReportDTO)
    assert report.all_passed is True
    assert report.is_conforming is True
    assert report.total_duration_seconds < 15.0
    assert len(report.checks) == 4
    assert all(c.passed and c.status_code == 200 for c in report.checks)
    SmokeRunner.assert_all_passed(report)


@respx.mock
def test_smoke_runner_falla_readiness_bd_caida() -> None:
    """2. Simula 503 (y 200 degradado) en /health/ready; valida fallo individual y global."""
    base = "http://localhost:8000"
    respx.get(f"{base}/health").mock(return_value=httpx.Response(200, json={"estado": "ok"}))
    respx.get(f"{base}/health/ready").mock(
        return_value=httpx.Response(503, json={"postgres": False, "redis": True})
    )
    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text=_PROMETHEUS_SAMPLE))
    respx.get(f"{base}/api/v1/auth/health").mock(
        return_value=httpx.Response(200, json={"auth": "ok"})
    )

    runner = SmokeRunner(request_timeout_seconds=1.0, max_retries=1, retry_backoff_seconds=0.0)
    report = runner.run_all_checks(base)

    assert report.all_passed is False
    assert report.is_conforming is False
    readiness = next(c for c in report.checks if c.check_name == "readiness")
    assert readiness.passed is False
    assert readiness.status_code == 503

    with pytest.raises(SmokeTestFailedError, match="readiness"):
        SmokeRunner.assert_all_passed(report)

    # Anti falso verde: HTTP 200 pero postgres=False en JSON también debe fallar
    respx.get(f"{base}/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": False, "redis": True})
    )
    degraded_check = runner.check_readiness(base)
    assert degraded_check.passed is False
    assert degraded_check.error_message is not None
    assert "postgres=false" in degraded_check.error_message


@respx.mock
def test_smoke_runner_timeout_excedido() -> None:
    """3. Simula timeout superior al límite configurado; captura limpia sin colapsar runner."""
    base = "http://localhost:8000"
    respx.get(f"{base}/health").mock(side_effect=httpx.ReadTimeout("timed out"))
    respx.get(f"{base}/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": True, "redis": True})
    )
    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text=_PROMETHEUS_SAMPLE))
    respx.get(f"{base}/api/v1/auth/health").mock(
        return_value=httpx.Response(200, json={"auth": "ok"})
    )

    runner = SmokeRunner(request_timeout_seconds=0.2, max_retries=2, retry_backoff_seconds=0.0)
    report = runner.run_all_checks(base)

    assert report.all_passed is False
    assert report.is_conforming is False
    liveness = next(c for c in report.checks if c.check_name == "liveness")
    assert liveness.passed is False
    assert liveness.status_code == 0
    assert liveness.error_message is not None
    assert "Timeout" in liveness.error_message


@respx.mock
def test_smoke_cli_exit_code_cero_y_uno(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """4. Valida CLI smoke_test_prod.py: exit 0 + stdout en éxito; exit 1 + stderr en fallo."""
    cli = _load_smoke_cli_module()
    base = "http://localhost:8000"

    respx.get(f"{base}/health").mock(return_value=httpx.Response(200, json={"estado": "ok"}))
    respx.get(f"{base}/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": True, "redis": True})
    )
    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text=_PROMETHEUS_SAMPLE))
    respx.get(f"{base}/api/v1/auth/health").mock(
        return_value=httpx.Response(200, json={"auth": "ok"})
    )

    report_out = tmp_path / "worm" / "smoke-report.json"
    exit_ok = cli.main(
        [
            "--base-url",
            base,
            "--timeout",
            "15.0",
            "--retries",
            "1",
            "--json-report",
            str(report_out),
        ]
    )
    captured_ok = capsys.readouterr()
    assert exit_ok == 0
    assert "OK smoke-" in captured_ok.out
    assert captured_ok.err == ""
    assert report_out.exists()
    data = json.loads(report_out.read_text(encoding="utf-8"))
    assert data["all_passed"] is True
    assert len(data["checks"]) == 4

    # Caso de fallo (500 en ruta API representativa) -> exit 1 y detalle en stderr
    respx.get(f"{base}/api/v1/auth/health").mock(return_value=httpx.Response(500, text="Error"))
    exit_fail = cli.main(["--base-url", base, "--retries", "0"])
    captured_fail = capsys.readouterr()
    assert exit_fail == 1
    assert "FAIL smoke-" in captured_fail.err
    assert "DEGRADADO api_route" in captured_fail.err


def test_inmutabilidad_dtos_smoke() -> None:
    """5. Confirma que SmokeCheckResultDTO y SmokeReportDTO tienen frozen=True."""
    check = SmokeCheckResultDTO(
        check_name="liveness",
        endpoint="/health",
        status_code=200,
        latency_ms=4.2,
        passed=True,
    )
    report = SmokeReportDTO(
        base_url="http://localhost:8000",
        total_duration_seconds=0.15,
        checks=[check],
        all_passed=True,
        is_conforming=True,
    )

    with pytest.raises(ValidationError):
        check.passed = False

    with pytest.raises(ValidationError):
        report.is_conforming = False


@respx.mock
def test_smoke_bordes_reintento_prometheus_invalido_y_cli_help(tmp_path: Path) -> None:
    """Cubre reintentos exitosos, rechazo de /metrics HTML/vacío, argumentos inválidos y --help."""
    base = "http://localhost:8000"

    # Reintento transitorio: falla 1 vez con 503 y pasa en el segundo intento
    liveness_route = respx.get(f"{base}/health").mock(
        side_effect=[
            httpx.Response(503, text="starting"),
            httpx.Response(200, json={"estado": "ok"}),
        ]
    )
    runner = SmokeRunner(request_timeout_seconds=1.0, max_retries=2, retry_backoff_seconds=0.0)
    res_retry = runner.check_liveness(base)
    assert res_retry.passed is True
    assert liveness_route.call_count == 2

    # /metrics con HTML o vacío debe fallar
    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    assert runner.check_metrics(base).passed is False

    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(200, text="   "))
    assert runner.check_metrics(base).passed is False

    respx.get(f"{base}/metrics").mock(return_value=httpx.Response(404, text="Not found"))
    assert runner.check_metrics(base).passed is False

    # Readiness no JSON
    respx.get(f"{base}/health/ready").mock(return_value=httpx.Response(200, text="not-json"))
    assert runner.check_readiness(base).passed is False

    # Liveness 404 y NetworkError
    respx.get(f"{base}/health").mock(return_value=httpx.Response(404))
    assert runner.check_liveness(base).passed is False

    respx.get(f"{base}/api/v1/auth/health").mock(side_effect=httpx.ConnectError("refused"))
    assert runner.check_api_route(base).passed is False

    # Validaciones de constructor y assert_all_passed vacío
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        SmokeRunner(request_timeout_seconds=0)
    with pytest.raises(ValueError, match="max_retries"):
        SmokeRunner(max_retries=-1)

    empty_report = SmokeReportDTO(
        base_url=base,
        total_duration_seconds=0.1,
        checks=[],
        all_passed=False,
        is_conforming=False,
    )
    with pytest.raises(SmokeTestFailedError, match="no contiene verificaciones"):
        SmokeRunner.assert_all_passed(empty_report)

    # Timeout global excedido en report aunque todos los checks individuales pasaron
    check_ok = SmokeCheckResultDTO(
        check_name="liveness",
        endpoint="/health",
        status_code=200,
        latency_ms=10.0,
        passed=True,
    )
    slow_report = SmokeReportDTO(
        base_url=base,
        total_duration_seconds=18.5,
        checks=[check_ok],
        all_passed=True,
        is_conforming=False,
    )
    with pytest.raises(SmokeTestFailedError, match="excedió presupuesto"):
        SmokeRunner.assert_all_passed(slow_report)

    # Exportación WORM
    exported = SmokeRunner.export_report_json(slow_report, tmp_path / "smoke.json")
    assert exported.exists()

    # Validación CLI argumentos inválidos y --help real con python3
    cli = _load_smoke_cli_module()
    assert cli.main(["--timeout", "0"]) == 1
    assert cli.main(["--retries", "-1"]) == 1

    script_path = Path(__file__).resolve().parents[4] / "scripts" / "smoke_test_prod.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--base-url" in proc.stdout
