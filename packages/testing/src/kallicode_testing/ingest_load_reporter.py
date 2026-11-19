"""Analizador y Reportero de Carga del Pipeline de Ingesta y Vectorización (KC-QA-B6-05).

Ticket: KC-QA-B6-05
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Propósito: Procesar `ingest-summary.json` generado por `k6_ingest_pipeline.js`,
validar rendimiento LOC/s, chunks/s, latencia vectorial p95 (< 800ms), pico RSS
(< 1500 MB / 1.5 GB) y aislamiento multi-tenant estricto, exportando DTOs
Pydantic v2 inmutables (`frozen=True`) listos para sellado WORM.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

# Presupuestos Canónicos de Rendimiento y Memoria (KC-QA-B6-05)
MAX_INGEST_P95_MS: float = 800.0
MAX_MEMORY_RSS_MB: float = 1500.0
MAX_HTTP_ERROR_RATE: float = 0.0


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class IngestPerformanceBudgetExceededError(RuntimeError):
    """Lanzada si la ingesta excede memoria RSS, latencia p95, sufre fugas o falta de datos."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-05 (Inmutables)
# ---------------------------------------------------------------------------
class IngestBatchMetricDTO(BaseModel):
    """Métrica inmutable por lote/repositorio procesado en ingesta y vectorización."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    repo_name: str
    files_count: int
    loc_total: int
    chunks_vectorized: int
    duration_seconds: float
    loc_per_second: float
    memory_peak_mb: float
    passed: bool


class IngestPipelineReportDTO(BaseModel):
    """Reporte consolidado e inmutable de carga del pipeline de ingesta local."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"ingest-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_repos_processed: int
    avg_loc_per_second: float
    max_memory_rss_mb: float
    zero_tenant_leaks: bool
    is_conforming: bool
    avg_chunks_per_second: float = 0.0
    p95_latency_ms: float = 0.0
    batches: list[IngestBatchMetricDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers internos de extracción
# ---------------------------------------------------------------------------
def _extract_values(metric_block: Any) -> dict[str, Any]:
    if isinstance(metric_block, dict):
        values = metric_block.get("values")
        if isinstance(values, dict):
            return values
        return metric_block
    return {}


def _evaluate_batch_passed(duration_seconds: float, memory_peak_mb: float) -> bool:
    latency_ms = duration_seconds * 1000.0
    return latency_ms < MAX_INGEST_P95_MS and memory_peak_mb < MAX_MEMORY_RSS_MB


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class IngestLoadReporter:
    """Analiza métricas de ingesta k6, verifica aislamiento/OOM y exporta JSON WORM."""

    @staticmethod
    def parse_k6_summary(summary_data: dict[str, Any]) -> IngestPipelineReportDTO:
        """Parsea `ingest-summary.json` hacia un DTO inmutable `IngestPipelineReportDTO`."""
        if not isinstance(summary_data, dict):
            msg = f"Se esperaba dict para summary_data, recibido: {type(summary_data)}"
            raise TypeError(msg)

        parsed_batches: list[IngestBatchMetricDTO] = []
        raw_metrics = summary_data.get("metrics")
        metrics_dict = raw_metrics if isinstance(raw_metrics, dict) else {}

        # Ruta A: lista explícita de lotes/repositorios pre-agregada
        if isinstance(summary_data.get("batches"), list):
            for item in summary_data["batches"]:
                if isinstance(item, IngestBatchMetricDTO):
                    batch = item
                elif isinstance(item, dict):
                    files_count = int(item.get("files_count", 0))
                    loc_total = int(item.get("loc_total", 0))
                    chunks_vec = int(item.get("chunks_vectorized", 0))
                    dur_s = float(item.get("duration_seconds", 0.0))
                    loc_s = float(
                        item.get(
                            "loc_per_second",
                            loc_total / dur_s if dur_s > 0.0 else 0.0,
                        )
                    )
                    mem_mb = float(item.get("memory_peak_mb", 0.0))
                    passed = bool(item.get("passed", _evaluate_batch_passed(dur_s, mem_mb)))
                    batch = IngestBatchMetricDTO(
                        tenant_id=str(item.get("tenant_id", "unknown")),
                        repo_name=str(item.get("repo_name", "unknown")),
                        files_count=files_count,
                        loc_total=loc_total,
                        chunks_vectorized=chunks_vec,
                        duration_seconds=round(dur_s, 4),
                        loc_per_second=round(loc_s, 2),
                        memory_peak_mb=round(mem_mb, 2),
                        passed=passed,
                    )
                else:
                    msg = f"Tipo no soportado en batches: {type(item)}"
                    raise TypeError(msg)
                parsed_batches.append(batch)

            total_repos = len(parsed_batches)
            avg_loc_s = (
                round(sum(b.loc_per_second for b in parsed_batches) / total_repos, 2)
                if total_repos > 0
                else 0.0
            )
            avg_chunks_s = (
                round(
                    sum(
                        (b.chunks_vectorized / b.duration_seconds)
                        if b.duration_seconds > 0.0
                        else 0.0
                        for b in parsed_batches
                    )
                    / total_repos,
                    2,
                )
                if total_repos > 0
                else 0.0
            )
            max_mem = (
                round(max(b.memory_peak_mb for b in parsed_batches), 2) if total_repos > 0 else 0.0
            )
            p95_ms = float(
                summary_data.get(
                    "p95_latency_ms",
                    max((b.duration_seconds * 1000.0 for b in parsed_batches), default=0.0),
                )
            )
            zero_leaks = bool(summary_data.get("zero_tenant_leaks", True))
            http_err_rate = float(summary_data.get("http_error_rate", 0.0))

        # Ruta B: resumen nativo k6 generado por `k6_ingest_pipeline.js`
        else:
            dur_vals = _extract_values(
                metrics_dict.get("ingest_batch_duration") or metrics_dict.get("http_req_duration")
            )
            loc_rate_vals = _extract_values(metrics_dict.get("ingest_loc_per_second"))
            chunks_rate_vals = _extract_values(metrics_dict.get("ingest_chunks_per_second"))
            mem_vals = _extract_values(metrics_dict.get("ingest_memory_rss_mb"))
            leak_vals = _extract_values(metrics_dict.get("ingest_tenant_leak_count"))
            err_vals = _extract_values(
                metrics_dict.get("ingest_http_errors") or metrics_dict.get("http_req_failed")
            )

            p95_ms = round(float(dur_vals.get("p(95)", 0.0)), 3)
            avg_dur_s = max(float(dur_vals.get("avg", 0.0)) / 1000.0, 0.001)
            avg_loc_s = round(float(loc_rate_vals.get("avg", 0.0)), 2)
            avg_chunks_s = round(float(chunks_rate_vals.get("avg", 0.0)), 2)
            max_mem = round(
                float(mem_vals.get("max", mem_vals.get("value", 0.0))),
                2,
            )
            leak_count = float(leak_vals.get("count", 0.0))
            zero_leaks = leak_count == 0.0 and bool(summary_data.get("zero_tenant_leaks", True))
            http_err_rate = float(err_vals.get("rate", 0.0))

            req_count = int(
                dur_vals.get(
                    "count", _extract_values(metrics_dict.get("http_reqs")).get("count", 0)
                )
            )
            metadata = summary_data.get("kallicode_ingest_metadata")
            catalog = (
                metadata.get("repos_catalog")
                if isinstance(metadata, dict) and isinstance(metadata.get("repos_catalog"), list)
                else []
            )

            if req_count > 0 and catalog:
                # Cada repositorio del catálogo procesado al menos una vez en la ventana
                repos_used = catalog[: min(req_count, len(catalog))]
                for entry in repos_used:
                    if not isinstance(entry, dict):
                        continue
                    loc_total = int(entry.get("loc_total", 0))
                    chunks_vec = int(entry.get("chunks_vectorized", 0))
                    parsed_batches.append(
                        IngestBatchMetricDTO(
                            tenant_id=str(entry.get("tenant_id", "unknown")),
                            repo_name=str(entry.get("repo_name", "unknown")),
                            files_count=int(entry.get("files_count", 0)),
                            loc_total=loc_total,
                            chunks_vectorized=chunks_vec,
                            duration_seconds=round(avg_dur_s, 4),
                            loc_per_second=round(loc_total / avg_dur_s, 2),
                            memory_peak_mb=max_mem,
                            passed=(p95_ms < MAX_INGEST_P95_MS and max_mem < MAX_MEMORY_RSS_MB),
                        )
                    )
            elif req_count > 0 and dur_vals:
                loc_total = int(
                    _extract_values(metrics_dict.get("ingest_loc_total")).get("count", 0)
                )
                chunks_total = int(
                    _extract_values(metrics_dict.get("ingest_chunks_total")).get("count", 0)
                )
                parsed_batches.append(
                    IngestBatchMetricDTO(
                        tenant_id="tenant-ingest-global",
                        repo_name="synthetic-batch",
                        files_count=req_count,
                        loc_total=loc_total,
                        chunks_vectorized=chunks_total,
                        duration_seconds=round(avg_dur_s, 4),
                        loc_per_second=avg_loc_s,
                        memory_peak_mb=max_mem,
                        passed=(p95_ms < MAX_INGEST_P95_MS and max_mem < MAX_MEMORY_RSS_MB),
                    )
                )

            total_repos = len(parsed_batches)
            if avg_loc_s == 0.0 and total_repos > 0:
                avg_loc_s = round(
                    sum(b.loc_per_second for b in parsed_batches) / total_repos,
                    2,
                )

        is_conforming = (
            total_repos > 0
            and p95_ms < MAX_INGEST_P95_MS
            and max_mem < MAX_MEMORY_RSS_MB
            and zero_leaks
            and http_err_rate <= MAX_HTTP_ERROR_RATE
            and all(b.passed for b in parsed_batches)
        )

        return IngestPipelineReportDTO(
            total_repos_processed=total_repos,
            avg_loc_per_second=avg_loc_s,
            max_memory_rss_mb=max_mem,
            zero_tenant_leaks=zero_leaks,
            is_conforming=is_conforming,
            avg_chunks_per_second=avg_chunks_s,
            p95_latency_ms=round(p95_ms, 3),
            batches=parsed_batches,
        )

    @staticmethod
    def assert_ingest_sla_compliance(report: IngestPipelineReportDTO) -> None:
        """Verifica presupuestos de ingesta, memoria (< 1500 MB) y aislamiento multi-tenant."""
        if report.total_repos_processed <= 0:
            logger.error("ingest_load.empty_report", report_id=report.report_id)
            raise IngestPerformanceBudgetExceededError(
                "El reporte de ingesta no procesó repositorios (prohibido falso verde)."
            )

        violations: list[str] = []
        if report.max_memory_rss_mb >= MAX_MEMORY_RSS_MB:
            violations.append(
                f"Memoria RSS pico={report.max_memory_rss_mb:.2f}MB >= umbral "
                f"{MAX_MEMORY_RSS_MB:.2f}MB (1.5 GB)"
            )
        if report.p95_latency_ms >= MAX_INGEST_P95_MS:
            violations.append(
                f"Latencia p95={report.p95_latency_ms:.2f}ms >= umbral {MAX_INGEST_P95_MS:.2f}ms"
            )
        if not report.zero_tenant_leaks:
            violations.append("Se detectó fuga/colisión multi-tenant (zero_tenant_leaks=False)")
        if not report.is_conforming and not violations:
            violations.append("El reporte consolidado fue marcado con is_conforming=False")

        if violations:
            message = (
                f"Violación de presupuesto/aislamiento en ingesta ({report.report_id}):\n- "
                + "\n- ".join(violations)
            )
            logger.error(
                "ingest_load.sla_violated",
                report_id=report.report_id,
                max_memory_rss_mb=report.max_memory_rss_mb,
                p95_latency_ms=report.p95_latency_ms,
                zero_tenant_leaks=report.zero_tenant_leaks,
            )
            raise IngestPerformanceBudgetExceededError(message)

        logger.info(
            "ingest_load.sla_passed",
            report_id=report.report_id,
            total_repos=report.total_repos_processed,
            avg_loc_per_second=report.avg_loc_per_second,
            max_memory_rss_mb=report.max_memory_rss_mb,
        )

    @staticmethod
    def export_report_json(report: IngestPipelineReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte de ingesta a JSON determinista listo para sellado WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "ingest_load.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para validar `ingest-summary.json` y exportar reporte WORM."""
    parser = argparse.ArgumentParser(
        description="Validador de Carga del Pipeline de Ingesta k6 (KC-QA-B6-05)",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path("ingest-summary.json"),
        help="Ruta al archivo JSON generado por k6_ingest_pipeline.js.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("ingest-pipeline-report.json"),
        help="Ruta de salida para el reporte DTO normalizado (WORM-ready).",
    )
    parser.add_argument(
        "--check-sla",
        action="store_true",
        help="Retorna exit code 1 si se viola memoria, latencia o aislamiento multi-tenant.",
    )
    args = parser.parse_args(argv)

    if not args.summary_file.exists():
        logger.error("ingest_load.summary_not_found", path=str(args.summary_file))
        return 1

    raw_data = json.loads(args.summary_file.read_text(encoding="utf-8"))
    report = IngestLoadReporter.parse_k6_summary(raw_data)
    IngestLoadReporter.export_report_json(report, args.output_file)

    if args.check_sla:
        try:
            IngestLoadReporter.assert_ingest_sla_compliance(report)
        except IngestPerformanceBudgetExceededError:
            return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
