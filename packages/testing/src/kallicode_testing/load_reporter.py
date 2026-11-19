"""Analizador y Reportero de Pruebas de Carga k6 para Módulos Core (KC-QA-B6-01).

Ticket: KC-QA-B6-01
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Decisión Arquitectónica: D16 (p95 < 200ms en Kanban, p99 < 500ms global, 0% 5xx, 120 req/min/usuario)
Propósito: Parsear `k6-summary.json`, certificar cumplimiento estricto de SLAs sin falsos
verdes y exportar reportes DTO Pydantic v2 inmutables (frozen=True) listos para sellado WORM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

# Presupuestos Canónicos de Carga (Decisión D16)
MAX_KANBAN_P95_MS: float = 200.0
MAX_GLOBAL_P99_MS: float = 500.0
MAX_5XX_ERROR_RATE: float = 0.0
D16_RATE_LIMIT_REQ_PER_MIN: int = 120

_CANONICAL_ENDPOINTS: dict[str, tuple[str, str]] = {
    "kanban_board": ("/api/v1/tickets?vista=kanban", "GET"),
    "ticket_detail": ("/api/v1/tickets/KC-1001", "GET"),
    "gate_sign": ("/api/v1/tickets/KC-1001/gates/1/firma", "POST"),
}

_TAG_ENDPOINT_RE = re.compile(r"^http_req_duration\{[^}]*endpoint:([^,}]+)[^}]*\}$")
_CUSTOM_DURATION_RE = re.compile(r"^http_req_duration_([a-zA-Z0-9_]+)$")


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class LoadSlaExceededError(RuntimeError):
    """Lanzada cuando una métrica de carga k6 incumple los SLAs o no hay datos válidos."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-01 (Inmutables)
# ---------------------------------------------------------------------------
class K6MetricSummaryDTO(BaseModel):
    """Resumen inmutable de latencias y tasa de error 5xx por endpoint evaluado."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    endpoint: str
    method: str
    p90_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float
    passed_sla: bool


class LoadTestReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la ejecución de pruebas de carga k6."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"load-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    vus_max: int
    duration_seconds: float
    metrics: list[K6MetricSummaryDTO] = Field(default_factory=list)
    rate_limit_verified: bool
    is_conforming: bool


# ---------------------------------------------------------------------------
# Helpers internos de parseo y evaluación
# ---------------------------------------------------------------------------
def _is_kanban_endpoint(endpoint: str) -> bool:
    lowered = endpoint.lower()
    return "kanban" in lowered or "vista=kanban" in lowered


def _evaluate_sla(endpoint: str, p95_ms: float, p99_ms: float, error_rate: float) -> list[str]:
    """Retorna la lista de violaciones de SLA para un endpoint evaluado."""
    violations: list[str] = []
    if _is_kanban_endpoint(endpoint) and p95_ms >= MAX_KANBAN_P95_MS:
        violations.append(f"p95 Kanban={p95_ms:.2f}ms >= umbral {MAX_KANBAN_P95_MS:.2f}ms")
    if p99_ms >= MAX_GLOBAL_P99_MS:
        violations.append(f"p99={p99_ms:.2f}ms >= umbral {MAX_GLOBAL_P99_MS:.2f}ms")
    if error_rate > MAX_5XX_ERROR_RATE:
        violations.append(f"error_rate 5xx={error_rate:.4f} > umbral {MAX_5XX_ERROR_RATE:.4f}")
    return violations


def _extract_values(metric_block: Any) -> dict[str, Any]:
    if isinstance(metric_block, dict):
        values = metric_block.get("values")
        if isinstance(values, dict):
            return values
        return metric_block
    return {}


def _extract_error_rate(
    metrics_dict: dict[str, Any],
    endpoint_key: str,
) -> float:
    candidate_keys = [
        f"http_5xx_errors{{endpoint:{endpoint_key}}}",
        f"http_5xx_errors_{endpoint_key}",
        "http_5xx_errors",
    ]
    for key in candidate_keys:
        if key in metrics_dict:
            values = _extract_values(metrics_dict[key])
            if "rate" in values:
                return float(values["rate"])
            if "value" in values:
                return float(values["value"])
    return 0.0


def _extract_rate_limit_verified(
    summary_data: dict[str, Any],
    metrics_dict: dict[str, Any],
) -> bool:
    metadata = summary_data.get("kallicode_metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("rate_limit_verified"), bool):
        return bool(metadata["rate_limit_verified"])

    if isinstance(summary_data.get("rate_limit_verified"), bool):
        return bool(summary_data["rate_limit_verified"])

    d16_metric = metrics_dict.get("d16_rate_limit_verified")
    if isinstance(d16_metric, dict):
        values = _extract_values(d16_metric)
        passes = float(values.get("passes", 0.0))
        fails = float(values.get("fails", 0.0))
        if passes > 0.0 and fails == 0.0:
            return True
        if "rate" in values and "passes" not in values and "fails" not in values:
            return float(values["rate"]) >= 1.0

    hits_block = _extract_values(metrics_dict.get("d16_429_hits"))
    retry_block = _extract_values(metrics_dict.get("d16_retry_after_present"))
    hits_count = float(hits_block.get("count", 0.0))
    retry_count = float(retry_block.get("count", 0.0))
    return hits_count > 0.0 and retry_count > 0.0


def _build_endpoint_lookup(summary_data: dict[str, Any]) -> dict[str, tuple[str, str]]:
    lookup = dict(_CANONICAL_ENDPOINTS)
    metadata = summary_data.get("kallicode_metadata")
    if isinstance(metadata, dict):
        raw_endpoints = metadata.get("endpoints")
        if isinstance(raw_endpoints, list):
            for entry in raw_endpoints:
                if isinstance(entry, dict) and entry.get("key") and entry.get("endpoint"):
                    lookup[str(entry["key"])] = (
                        str(entry["endpoint"]),
                        str(entry.get("method", "GET")).upper(),
                    )
    return lookup


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class LoadReporter:
    """Procesa resúmenes k6, valida SLAs de Decisión D16 y exporta reportes WORM."""

    @staticmethod
    def parse_k6_summary(summary_data: dict[str, Any]) -> LoadTestReportDTO:
        """Parsea `k6-summary.json` hacia un DTO inmutable `LoadTestReportDTO`."""
        if not isinstance(summary_data, dict):
            msg = f"Se esperaba dict para summary_data, recibido: {type(summary_data)}"
            raise TypeError(msg)

        raw_metrics = summary_data.get("metrics")
        parsed_metrics: list[K6MetricSummaryDTO] = []

        if isinstance(raw_metrics, dict):
            endpoint_lookup = _build_endpoint_lookup(summary_data)
            discovered: dict[str, dict[str, Any]] = {}

            for metric_name, metric_block in raw_metrics.items():
                if not isinstance(metric_block, dict):
                    continue
                tag_match = _TAG_ENDPOINT_RE.match(metric_name)
                if tag_match:
                    ep_key = tag_match.group(1).strip()
                    discovered[ep_key] = _extract_values(metric_block)
                    continue

                custom_match = _CUSTOM_DURATION_RE.match(metric_name)
                if custom_match:
                    ep_key = custom_match.group(1).strip()
                    if ep_key not in discovered:
                        discovered[ep_key] = _extract_values(metric_block)

            # Fallback a métrica global http_req_duration si no hubo sub-métricas por endpoint
            if not discovered and "http_req_duration" in raw_metrics:
                global_values = _extract_values(raw_metrics["http_req_duration"])
                if global_values:
                    discovered["kanban_board"] = global_values

            for ep_key, values in discovered.items():
                endpoint_path, method = endpoint_lookup.get(ep_key, (ep_key, "GET"))
                p90_ms = round(float(values.get("p(90)", 0.0)), 3)
                p95_ms = round(float(values.get("p(95)", 0.0)), 3)
                p99_ms = round(float(values.get("p(99)", 0.0)), 3)
                error_rate = round(_extract_error_rate(raw_metrics, ep_key), 6)
                violations = _evaluate_sla(endpoint_path, p95_ms, p99_ms, error_rate)

                parsed_metrics.append(
                    K6MetricSummaryDTO(
                        endpoint=endpoint_path,
                        method=method,
                        p90_ms=p90_ms,
                        p95_ms=p95_ms,
                        p99_ms=p99_ms,
                        error_rate=error_rate,
                        passed_sla=len(violations) == 0,
                    )
                )

            vus_values = _extract_values(raw_metrics.get("vus_max"))
            vus_max = int(vus_values.get("max", summary_data.get("vus_max", 0)))
            rate_limit_verified = _extract_rate_limit_verified(summary_data, raw_metrics)
        elif isinstance(raw_metrics, list):
            for item in raw_metrics:
                if isinstance(item, K6MetricSummaryDTO):
                    ep = item.endpoint
                    method = item.method
                    p90 = item.p90_ms
                    p95 = item.p95_ms
                    p99 = item.p99_ms
                    err = item.error_rate
                elif isinstance(item, dict):
                    ep = str(item.get("endpoint", "unknown"))
                    method = str(item.get("method", "GET")).upper()
                    p90 = float(item.get("p90_ms", 0.0))
                    p95 = float(item.get("p95_ms", 0.0))
                    p99 = float(item.get("p99_ms", 0.0))
                    err = float(item.get("error_rate", 0.0))
                else:
                    msg = f"Formato no soportado en lista de métricas: {type(item)}"
                    raise TypeError(msg)

                violations = _evaluate_sla(ep, p95, p99, err)
                parsed_metrics.append(
                    K6MetricSummaryDTO(
                        endpoint=ep,
                        method=method,
                        p90_ms=round(p90, 3),
                        p95_ms=round(p95, 3),
                        p99_ms=round(p99, 3),
                        error_rate=round(err, 6),
                        passed_sla=len(violations) == 0,
                    )
                )

            vus_max = int(summary_data.get("vus_max", 0))
            rate_limit_verified = _extract_rate_limit_verified(summary_data, {})
        else:
            vus_max = int(summary_data.get("vus_max", 0))
            rate_limit_verified = _extract_rate_limit_verified(summary_data, {})

        state_block = summary_data.get("state")
        if isinstance(state_block, dict) and "testRunDurationMs" in state_block:
            duration_seconds = round(float(state_block["testRunDurationMs"]) / 1000.0, 3)
        else:
            duration_seconds = round(float(summary_data.get("duration_seconds", 0.0)), 3)

        is_conforming = len(parsed_metrics) > 0 and all(m.passed_sla for m in parsed_metrics)

        return LoadTestReportDTO(
            vus_max=vus_max,
            duration_seconds=duration_seconds,
            metrics=parsed_metrics,
            rate_limit_verified=rate_limit_verified,
            is_conforming=is_conforming,
        )

    @staticmethod
    def assert_zero_sla_violations(report: LoadTestReportDTO) -> None:
        """Verifica 0 violaciones de SLA; lanza `LoadSlaExceededError` ante cualquier desvío."""
        if not report.metrics:
            logger.error("load_test.empty_metrics", report_id=report.report_id)
            raise LoadSlaExceededError(
                "El reporte de carga no contiene métricas de endpoints (prohibido falso verde)."
            )

        violated_details: list[str] = []
        for metric in report.metrics:
            metric_violations = _evaluate_sla(
                metric.endpoint,
                metric.p95_ms,
                metric.p99_ms,
                metric.error_rate,
            )
            if not metric.passed_sla and not metric_violations:
                metric_violations.append("marcado con passed_sla=False")

            for detail in metric_violations:
                violated_details.append(f"- [{metric.method} {metric.endpoint}]: {detail}")

        if violated_details or not report.is_conforming:
            if not violated_details:
                violated_details.append("- El reporte general fue marcado con is_conforming=False")
            message = (
                f"Se detectaron violaciones de SLA de carga en el reporte {report.report_id}:\n"
                + "\n".join(violated_details)
            )
            logger.error(
                "load_test.sla_violated",
                report_id=report.report_id,
                violations_count=len(violated_details),
            )
            raise LoadSlaExceededError(message)

        logger.info(
            "load_test.sla_passed",
            report_id=report.report_id,
            vus_max=report.vus_max,
            endpoints_count=len(report.metrics),
            rate_limit_verified=report.rate_limit_verified,
        )

    @staticmethod
    def export_report_json(report: LoadTestReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte estructurado e inmutable a JSON determinista para sellado WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "load_test.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para validar `k6-summary.json` y exportar reporte WORM."""
    parser = argparse.ArgumentParser(
        description="Validador de SLAs de Carga k6 (KC-QA-B6-01 / Decisión D16)",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path("k6-summary.json"),
        help="Ruta al archivo JSON generado por k6 handleSummary.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Ruta de salida opcional para el reporte DTO normalizado.",
    )
    parser.add_argument(
        "--check-sla",
        action="store_true",
        help="Falla con exit code 1 si se detecta alguna violación de SLA.",
    )
    args = parser.parse_args(argv)

    if not args.summary_file.exists():
        logger.error("load_test.summary_not_found", path=str(args.summary_file))
        return 1

    raw_data = json.loads(args.summary_file.read_text(encoding="utf-8"))
    report = LoadReporter.parse_k6_summary(raw_data)

    if args.output_file is not None:
        LoadReporter.export_report_json(report, args.output_file)

    if args.check_sla:
        try:
            LoadReporter.assert_zero_sla_violations(report)
        except LoadSlaExceededError:
            return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
