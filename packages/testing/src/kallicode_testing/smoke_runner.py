"""Smoke Test Runner Ultrarrápido para Despliegues (KC-QA-B6-06).

Ticket: KC-QA-B6-06
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Propósito: Verificar la salud operacional inmediata post-despliegue (< 15 segundos)
mediante checks sobre `/health`, `/health/ready`, `/metrics` (OpenMetrics/Prometheus)
y ruta API representativa, emitiendo DTOs Pydantic v2 inmutables (`frozen=True`).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

DEFAULT_REQUEST_TIMEOUT_S: float = 3.0
DEFAULT_MAX_RETRIES: int = 2
DEFAULT_TOTAL_BUDGET_S: float = 15.0
DEFAULT_API_ROUTE: str = "/api/v1/auth/health"

_PROMETHEUS_METRIC_LINE_RE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})?\s+"
    r"[-+]?([0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?|NaN|[+-]?Inf)(\s+[0-9.]+)?$"
)


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class SmokeTestFailedError(RuntimeError):
    """Lanzada cuando una o más verificaciones de humo fallan o exceden el presupuesto."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-06 (Inmutables)
# ---------------------------------------------------------------------------
class SmokeCheckResultDTO(BaseModel):
    """Resultado inmutable de una verificación individual de smoke test."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    check_name: str
    endpoint: str
    status_code: int
    latency_ms: float
    passed: bool
    error_message: str | None = None


class SmokeReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la ejecución de smoke tests."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"smoke-{uuid.uuid4().hex[:12]}")
    executed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    base_url: str
    total_duration_seconds: float
    checks: list[SmokeCheckResultDTO] = Field(default_factory=list)
    all_passed: bool
    is_conforming: bool


# ---------------------------------------------------------------------------
# Helpers internos de validación
# ---------------------------------------------------------------------------
def _is_valid_prometheus_text(body: str) -> bool:
    """Valida que el payload responda en formato Prometheus / OpenMetrics real."""
    stripped = body.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith(("<!doctype", "<html")):
        return False

    has_header_directive = False
    has_sample_line = False
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("# HELP ", "# TYPE ")):
            has_header_directive = True
            continue
        if line.startswith("#"):
            continue
        if _PROMETHEUS_METRIC_LINE_RE.match(line):
            has_sample_line = True

    return has_header_directive or has_sample_line


# ---------------------------------------------------------------------------
# Runner Oficial de Pruebas de Humo
# ---------------------------------------------------------------------------
class SmokeRunner:
    """Ejecuta pruebas de humo post-despliegue con timeout estricto y reintentos rápidos."""

    def __init__(
        self,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = 0.2,
        api_route: str = DEFAULT_API_ROUTE,
    ) -> None:
        if request_timeout_seconds <= 0:
            msg = "request_timeout_seconds debe ser positivo"
            raise ValueError(msg)
        if max_retries < 0:
            msg = "max_retries debe ser >= 0"
            raise ValueError(msg)

        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.api_route = api_route if api_route.startswith("/") else f"/{api_route}"

    def _request_with_retry(
        self,
        base_url: str,
        endpoint: str,
    ) -> tuple[httpx.Response | None, float, str | None]:
        """Ejecuta GET con timeout por intento y reintentos ante red/timeout o 5xx."""
        cleaned_base = base_url.rstrip("/")
        url = f"{cleaned_base}{endpoint}"
        last_response: httpx.Response | None = None
        last_error: str | None = None
        started = time.perf_counter()

        with httpx.Client(timeout=self.request_timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.get(url)
                    last_response = response
                    if response.status_code < 500:
                        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
                        return response, elapsed_ms, None
                    last_error = f"HTTP {response.status_code} desde {endpoint}"
                except httpx.TimeoutException as exc:
                    last_error = f"Timeout (> {self.request_timeout_seconds}s) en {endpoint}: {exc}"
                except httpx.RequestError as exc:
                    last_error = f"Error de red en {endpoint}: {exc}"

                if attempt < self.max_retries and self.retry_backoff_seconds > 0:
                    time.sleep(self.retry_backoff_seconds)

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return last_response, elapsed_ms, last_error

    def check_liveness(self, base_url: str) -> SmokeCheckResultDTO:
        """Verifica `GET /health` exigiendo HTTP 200 sin aceptar 404 ni 5xx."""
        endpoint = "/health"
        response, latency_ms, net_error = self._request_with_retry(base_url, endpoint)
        status_code = response.status_code if response is not None else 0

        if net_error is not None or response is None:
            return SmokeCheckResultDTO(
                check_name="liveness",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=net_error or "Sin respuesta HTTP en liveness",
            )

        if status_code != 200:
            return SmokeCheckResultDTO(
                check_name="liveness",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Se esperaba HTTP 200 en {endpoint}, recibido {status_code}",
            )

        return SmokeCheckResultDTO(
            check_name="liveness",
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            passed=True,
        )

    def check_readiness(self, base_url: str) -> SmokeCheckResultDTO:
        """Verifica `GET /health/ready` confirmando HTTP 200 y conexión activa a Postgres/Redis."""
        endpoint = "/health/ready"
        response, latency_ms, net_error = self._request_with_retry(base_url, endpoint)
        status_code = response.status_code if response is not None else 0

        if net_error is not None or response is None:
            return SmokeCheckResultDTO(
                check_name="readiness",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=net_error or "Sin respuesta HTTP en readiness",
            )

        if status_code != 200:
            return SmokeCheckResultDTO(
                check_name="readiness",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Readiness degradado (HTTP {status_code}) en {endpoint}",
            )

        try:
            body: Any = response.json()
        except ValueError:
            return SmokeCheckResultDTO(
                check_name="readiness",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Respuesta no JSON en {endpoint}",
            )

        if isinstance(body, dict):
            degraded: list[str] = []
            if "postgres" in body and body["postgres"] is not True:
                degraded.append("postgres=false")
            if "redis" in body and body["redis"] is not True:
                degraded.append("redis=false")
            if degraded:
                return SmokeCheckResultDTO(
                    check_name="readiness",
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    passed=False,
                    error_message=f"Dependencias no listas en {endpoint}: {', '.join(degraded)}",
                )

        return SmokeCheckResultDTO(
            check_name="readiness",
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            passed=True,
        )

    def check_metrics(self, base_url: str) -> SmokeCheckResultDTO:
        """Verifica `GET /metrics` exigiendo HTTP 200 y formato Prometheus/OpenMetrics válido."""
        endpoint = "/metrics"
        response, latency_ms, net_error = self._request_with_retry(base_url, endpoint)
        status_code = response.status_code if response is not None else 0

        if net_error is not None or response is None:
            return SmokeCheckResultDTO(
                check_name="metrics",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=net_error or "Sin respuesta HTTP en metrics",
            )

        if status_code != 200:
            return SmokeCheckResultDTO(
                check_name="metrics",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Se esperaba HTTP 200 en {endpoint}, recibido {status_code}",
            )

        if not _is_valid_prometheus_text(response.text):
            return SmokeCheckResultDTO(
                check_name="metrics",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Contenido inválido (no OpenMetrics/Prometheus) en {endpoint}",
            )

        return SmokeCheckResultDTO(
            check_name="metrics",
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            passed=True,
        )

    def check_api_route(self, base_url: str) -> SmokeCheckResultDTO:
        """Verifica ruta API representativa confirmando enrutamiento FastAPI activo."""
        endpoint = self.api_route
        response, latency_ms, net_error = self._request_with_retry(base_url, endpoint)
        status_code = response.status_code if response is not None else 0

        if net_error is not None or response is None:
            return SmokeCheckResultDTO(
                check_name="api_route",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=net_error or f"Sin respuesta HTTP en {endpoint}",
            )

        if status_code != 200:
            return SmokeCheckResultDTO(
                check_name="api_route",
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                passed=False,
                error_message=f"Se esperaba HTTP 200 en {endpoint}, recibido {status_code}",
            )

        return SmokeCheckResultDTO(
            check_name="api_route",
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            passed=True,
        )

    def run_all_checks(
        self,
        base_url: str,
        timeout_seconds: float = DEFAULT_TOTAL_BUDGET_S,
    ) -> SmokeReportDTO:
        """Ejecuta toda la batería de humo validando cumplimiento de presupuesto (< 15s)."""
        cleaned_base = base_url.rstrip("/")
        started = time.perf_counter()

        checks = [
            self.check_liveness(cleaned_base),
            self.check_readiness(cleaned_base),
            self.check_metrics(cleaned_base),
            self.check_api_route(cleaned_base),
        ]

        total_duration = round(time.perf_counter() - started, 4)
        all_passed = len(checks) > 0 and all(item.passed for item in checks)
        is_conforming = all_passed and total_duration < timeout_seconds

        report = SmokeReportDTO(
            base_url=cleaned_base,
            total_duration_seconds=total_duration,
            checks=checks,
            all_passed=all_passed,
            is_conforming=is_conforming,
        )

        if is_conforming:
            logger.info(
                "smoke.suite_passed",
                report_id=report.report_id,
                base_url=cleaned_base,
                duration_s=total_duration,
            )
        else:
            failed_names = [c.check_name for c in checks if not c.passed]
            logger.error(
                "smoke.suite_failed",
                report_id=report.report_id,
                base_url=cleaned_base,
                duration_s=total_duration,
                failed_checks=failed_names,
            )

        return report

    @staticmethod
    def assert_all_passed(report: SmokeReportDTO) -> None:
        """Lanza SmokeTestFailedError si algún check falló o superó el presupuesto total."""
        if not report.checks:
            raise SmokeTestFailedError("El reporte de humo no contiene verificaciones ejecutadas.")

        failed = [c for c in report.checks if not c.passed]
        if failed or not report.all_passed or not report.is_conforming:
            details = [
                f"- {c.check_name} ({c.endpoint}): status={c.status_code}, "
                f"error={c.error_message or 'fallo sin detalle'}"
                for c in failed
            ]
            if not details:
                details.append(
                    f"- Duración total {report.total_duration_seconds:.3f}s excedió presupuesto"
                )
            msg = f"Smoke test fallido contra {report.base_url}:\n" + "\n".join(details)
            raise SmokeTestFailedError(msg)

    @staticmethod
    def export_report_json(report: SmokeReportDTO, output_path: Path | str) -> Path:
        """Serializa el SmokeReportDTO a JSON determinista para auditoría e ingesta WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "smoke.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path
