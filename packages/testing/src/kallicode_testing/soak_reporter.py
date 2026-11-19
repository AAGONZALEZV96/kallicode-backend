"""Analizador de Fugas de Memoria y Resistencia (Soak Testing) (KC-QA-B6-09).

Ticket: KC-QA-B6-09
Épica: EQA1 — CI/CD y Entornos de Prueba de la Plataforma (Hito M6 GA)
Propósito:
- Parsear `soak-summary.json` con series de muestras RSS y métricas k6.
- Calcular `delta_rss_pct` y la pendiente lineal `memory_slope_mb_per_hour` tras el warm-up.
- Certificar 0 errores OOMKilled, 0.0% HTTP 5xx y degradación de latencia p95 <= 15%.
- Bloquear con `MemoryLeakDetectedError` ante fuga creciente o reporte vacío.
- Exportar `soak-report.json` con DTOs Pydantic v2 inmutables (`frozen=True`).
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

MAX_DELTA_RSS_PCT: float = 10.0
MAX_SLOPE_MB_PER_HOUR: float = 5.0
MAX_LATENCY_DEGRADATION_PCT: float = 15.0
MAX_HTTP_5XX_RATE: float = 0.0


# ---------------------------------------------------------------------------
# Excepción de Dominio
# ---------------------------------------------------------------------------
class MemoryLeakDetectedError(RuntimeError):
    """Lanzada si el soak test evidencia fuga de memoria, OOMKilled, 5xx o degradación."""


# ---------------------------------------------------------------------------
# DTOs Oficiales Inmutables (Pydantic v2)
# ---------------------------------------------------------------------------
class MemorySampleDTO(BaseModel):
    """Muestra puntual e inmutable del consumo RSS durante el soak test."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    timestamp_seconds: float
    memory_rss_mb: float
    active_vus: int


class SoakTestReportDTO(BaseModel):
    """Informe consolidado e inmutable de resistencia y estabilidad de memoria."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"soak-{uuid.uuid4().hex[:12]}")
    executed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    duration_seconds: float
    initial_rss_mb: float
    final_rss_mb: float
    delta_rss_pct: float
    memory_slope_mb_per_hour: float
    p95_latency_ms: float
    is_leak_detected: bool
    is_conforming: bool
    http_5xx_rate: float = 0.0
    oom_killed_count: int = 0
    latency_degradation_pct: float = 0.0
    samples: list[MemorySampleDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers Internos
# ---------------------------------------------------------------------------
def _extract_values(metric_block: Any) -> dict[str, Any]:
    """Extrae el subdiccionario `values` o retorna el bloque si ya es plano."""
    if isinstance(metric_block, dict):
        values = metric_block.get("values")
        if isinstance(values, dict):
            return values
        return metric_block
    return {}


def _extract_samples(summary_data: dict[str, Any]) -> list[MemorySampleDTO]:
    """Extrae y ordena cronológicamente las muestras RSS presentes en `summary_data`."""
    raw_samples = summary_data.get("memory_samples")
    if not isinstance(raw_samples, list):
        metadata = summary_data.get("kallicode_soak_metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("memory_samples"), list):
            raw_samples = metadata["memory_samples"]
        else:
            raw_samples = []

    parsed: list[MemorySampleDTO] = []
    for item in raw_samples:
        if isinstance(item, MemorySampleDTO):
            parsed.append(item)
        elif isinstance(item, dict):
            ts = float(item.get("timestamp_seconds", item.get("timestamp", 0.0)))
            rss = float(item.get("memory_rss_mb", item.get("rss_mb", 0.0)))
            vus = int(item.get("active_vus", item.get("vus", 0)))
            parsed.append(
                MemorySampleDTO(
                    timestamp_seconds=ts,
                    memory_rss_mb=rss,
                    active_vus=vus,
                )
            )
    return sorted(parsed, key=lambda sample: sample.timestamp_seconds)


def _select_post_warmup_samples(
    samples: list[MemorySampleDTO],
    warmup_seconds: float,
) -> list[MemorySampleDTO]:
    """Descarta el warm-up inicial cuando quedan al menos 2 muestras posteriores."""
    if warmup_seconds <= 0 or len(samples) < 4:
        return samples
    post = [sample for sample in samples if sample.timestamp_seconds >= warmup_seconds]
    return post if len(post) >= 2 else samples


def _extract_latency_degradation_pct(summary_data: dict[str, Any]) -> tuple[float, float]:
    """Calcula `(p95_global_ms, latency_degradation_pct)` desde métricas k6 o directas."""
    metrics = summary_data.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    req_values = _extract_values(metrics.get("http_req_duration"))
    p95_global = float(
        summary_data.get("p95_latency_ms", req_values.get("p(95)", 0.0)),
    )

    if "latency_degradation_pct" in summary_data:
        return p95_global, float(summary_data["latency_degradation_pct"])

    warmup_vals = _extract_values(metrics.get("soak_warmup_duration"))
    late_vals = _extract_values(metrics.get("soak_final_duration"))
    warmup_p95 = float(summary_data.get("warmup_p95_ms", warmup_vals.get("p(95)", 0.0)))
    final_p95 = float(summary_data.get("final_p95_ms", late_vals.get("p(95)", 0.0)))

    if warmup_p95 > 0.0 and final_p95 > 0.0:
        degradation = ((final_p95 - warmup_p95) / warmup_p95) * 100.0
        return (p95_global or final_p95), round(degradation, 2)
    return p95_global, 0.0


# ---------------------------------------------------------------------------
# Clase Principal SoakReporter
# ---------------------------------------------------------------------------
class SoakReporter:
    """Analizador y compuerta dura de resistencia y memoria para Soak Tests."""

    MAX_DELTA_RSS_PCT: float = MAX_DELTA_RSS_PCT
    MAX_SLOPE_MB_PER_HOUR: float = MAX_SLOPE_MB_PER_HOUR
    MAX_LATENCY_DEGRADATION_PCT: float = MAX_LATENCY_DEGRADATION_PCT

    def calculate_memory_slope(self, samples: list[MemorySampleDTO]) -> float:
        """Calcula la pendiente lineal OLS del consumo RSS en MB por hora."""
        if len(samples) < 2:
            return 0.0

        ordered = sorted(samples, key=lambda item: item.timestamp_seconds)
        n = len(ordered)
        times_h = [item.timestamp_seconds / 3600.0 for item in ordered]
        rss_vals = [item.memory_rss_mb for item in ordered]

        mean_t = sum(times_h) / n
        mean_r = sum(rss_vals) / n

        denominator = sum((t - mean_t) ** 2 for t in times_h)
        if denominator <= 0.0:
            return 0.0

        numerator = sum((t - mean_t) * (r - mean_r) for t, r in zip(times_h, rss_vals, strict=True))
        return round(numerator / denominator, 4)

    def parse_k6_summary(self, summary_data: dict[str, Any]) -> SoakTestReportDTO:
        """Transforma `soak-summary.json` en un `SoakTestReportDTO` validado."""
        if not isinstance(summary_data, dict):
            raise TypeError("summary_data debe ser un diccionario JSON de k6 o equivalente.")

        metadata = summary_data.get("kallicode_soak_metadata")
        state = summary_data.get("state")
        warmup_seconds = (
            float(metadata.get("warmup_seconds", 0.0))
            if isinstance(metadata, dict)
            else float(summary_data.get("warmup_seconds", 0.0))
        )

        samples = _extract_samples(summary_data)
        duration_seconds = float(
            summary_data.get(
                "duration_seconds",
                (
                    metadata.get("total_duration_seconds", 0.0)
                    if isinstance(metadata, dict)
                    else 0.0
                ),
            )
        )
        if duration_seconds <= 0.0 and isinstance(state, dict):
            duration_seconds = float(state.get("testRunDurationMs", 0.0)) / 1000.0
        if duration_seconds <= 0.0 and len(samples) >= 2:
            duration_seconds = max(
                0.0, samples[-1].timestamp_seconds - samples[0].timestamp_seconds
            )

        metrics = summary_data.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}

        http_5xx_vals = _extract_values(metrics.get("http_5xx_errors"))
        http_5xx_rate = float(summary_data.get("http_5xx_rate", http_5xx_vals.get("rate", 0.0)))

        oom_vals = _extract_values(metrics.get("soak_oom_killed_count"))
        oom_killed_count = int(summary_data.get("oom_killed_count", oom_vals.get("count", 0)))

        p95_latency_ms, latency_degradation_pct = _extract_latency_degradation_pct(summary_data)

        if samples:
            eval_samples = _select_post_warmup_samples(samples, warmup_seconds)
            initial_rss_mb = eval_samples[0].memory_rss_mb
            final_rss_mb = eval_samples[-1].memory_rss_mb
            slope_mb_per_hour = self.calculate_memory_slope(eval_samples)
        else:
            initial_rss_mb = float(summary_data.get("initial_rss_mb", 0.0))
            final_rss_mb = float(summary_data.get("final_rss_mb", 0.0))
            slope_mb_per_hour = 0.0

        if initial_rss_mb > 0.0:
            delta_rss_pct = round(((final_rss_mb - initial_rss_mb) / initial_rss_mb) * 100.0, 2)
        else:
            delta_rss_pct = 0.0

        has_valid_memory_series = (
            len(samples) >= 2 and initial_rss_mb > 0.0 and duration_seconds > 0.0
        )
        slope_indicates_leak = (
            slope_mb_per_hour > self.MAX_SLOPE_MB_PER_HOUR and delta_rss_pct >= 5.0
        )
        is_leak_detected = delta_rss_pct >= self.MAX_DELTA_RSS_PCT or slope_indicates_leak

        is_conforming = (
            has_valid_memory_series
            and not is_leak_detected
            and oom_killed_count == 0
            and http_5xx_rate <= MAX_HTTP_5XX_RATE
            and latency_degradation_pct <= self.MAX_LATENCY_DEGRADATION_PCT
        )

        report = SoakTestReportDTO(
            duration_seconds=round(duration_seconds, 2),
            initial_rss_mb=round(initial_rss_mb, 2),
            final_rss_mb=round(final_rss_mb, 2),
            delta_rss_pct=delta_rss_pct,
            memory_slope_mb_per_hour=slope_mb_per_hour,
            p95_latency_ms=round(p95_latency_ms, 2),
            is_leak_detected=is_leak_detected,
            is_conforming=is_conforming,
            http_5xx_rate=round(http_5xx_rate, 6),
            oom_killed_count=oom_killed_count,
            latency_degradation_pct=latency_degradation_pct,
            samples=samples,
        )
        logger.info(
            "soak_test.summary_parsed",
            report_id=report.report_id,
            duration_seconds=report.duration_seconds,
            delta_rss_pct=report.delta_rss_pct,
            slope_mb_per_hour=report.memory_slope_mb_per_hour,
            is_leak_detected=report.is_leak_detected,
            is_conforming=report.is_conforming,
        )
        return report

    def assert_no_memory_leak(self, report: SoakTestReportDTO) -> None:
        """Compuerta dura contra fugas de memoria, OOMKilled, 5xx o reportes vacíos."""
        reasons: list[str] = []

        if report.duration_seconds <= 0.0:
            reasons.append("Duración de ejecución inválida (0 segundos)")
        if len(report.samples) < 2 or report.initial_rss_mb <= 0.0:
            reasons.append("Reporte sin serie mínima de muestras de memoria RSS")
        if report.delta_rss_pct >= self.MAX_DELTA_RSS_PCT:
            reasons.append(
                f"Crecimiento de memoria RSS excede umbral: {report.delta_rss_pct:.2f}% "
                f"(máximo < {self.MAX_DELTA_RSS_PCT:.0f}%)"
            )
        if report.memory_slope_mb_per_hour > self.MAX_SLOPE_MB_PER_HOUR and (
            report.delta_rss_pct >= 5.0
        ):
            reasons.append(
                f"Pendiente positiva de fuga detectada: {report.memory_slope_mb_per_hour:.2f} MB/h"
            )
        if report.is_leak_detected and not any(
            "memoria" in r.lower() or "fuga" in r.lower() for r in reasons
        ):
            reasons.append("Indicador is_leak_detected activo")
        if report.oom_killed_count > 0:
            reasons.append(f"Eventos OOMKilled detectados: {report.oom_killed_count}")
        if report.http_5xx_rate > MAX_HTTP_5XX_RATE:
            reasons.append(f"Errores HTTP 5xx sostenidos: rate={report.http_5xx_rate:.4f}")
        if report.latency_degradation_pct > self.MAX_LATENCY_DEGRADATION_PCT:
            reasons.append(
                f"Degradación de latencia p95 excede 15%: {report.latency_degradation_pct:.2f}%"
            )
        if not report.is_conforming and not reasons:
            reasons.append("El reporte de soak test reportó is_conforming=False")

        if reasons:
            detail = "; ".join(reasons)
            logger.error(
                "soak_test.memory_leak_or_sla_blocked",
                report_id=report.report_id,
                reasons=reasons,
            )
            raise MemoryLeakDetectedError(f"Soak Test bloqueado: {detail}")

    def export_report_json(self, report: SoakTestReportDTO, output_path: Path) -> Path:
        """Exporta `soak-report.json` estructurado e inmutable para sellado WORM."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
        target.write_text(f"{payload}\n", encoding="utf-8")
        logger.info("soak_test.report_exported", report_id=report.report_id, path=str(target))
        return target


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para analizar `soak-summary.json` y certificar estabilidad."""
    parser = argparse.ArgumentParser(
        description="Analizador de Resistencia y Fuga de Memoria Soak k6 (KC-QA-B6-09)",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path("soak-summary.json"),
        help="Ruta al archivo JSON generado por k6_soak_test.js.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("soak-report.json"),
        help="Ruta de salida para el reporte DTO normalizado.",
    )
    parser.add_argument(
        "--check-soak",
        action="store_true",
        help="Retorna exit code 1 si se detecta fuga de memoria o fallo de estabilidad.",
    )
    args = parser.parse_args(argv)

    if not args.summary_file.exists():
        logger.error("soak_test.summary_not_found", path=str(args.summary_file))
        return 1

    raw_data = json.loads(args.summary_file.read_text(encoding="utf-8"))
    reporter = SoakReporter()
    report = reporter.parse_k6_summary(raw_data)
    reporter.export_report_json(report, args.output_file)

    if args.check_soak:
        try:
            reporter.assert_no_memory_leak(report)
        except MemoryLeakDetectedError:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
