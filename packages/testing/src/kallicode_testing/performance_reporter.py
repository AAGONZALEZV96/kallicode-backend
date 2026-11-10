"""Módulo de Análisis y Reporte de Rendimiento de Carga Inicial (Core Web Vitals).

Ticket: KC-QA-B5-12
Épica: EQA5 - Motor de Pruebas Autónomo y E2E
Propósito: Evaluar, procesar y certificar las métricas de Core Web Vitals en el Portal Cliente
(TTFB <= 600ms, FCP < 1.2s, LCP <= 2.0s, CLS <= 0.1), garantizando una experiencia de usuario
ultrarrápida y estable en un entorno de medición 100% Cero-Nube.
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

# Presupuestos Canónicos de Rendimiento (Core Web Vitals)
MAX_TTFB_MS: float = 600.0
MAX_FCP_MS: float = 1200.0  # < 1.2 segundos (criterio duro)
MAX_LCP_MS: float = 2000.0  # <= 2.0 segundos
MAX_CLS: float = 0.1


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class PerformanceBudgetExceededError(RuntimeError):
    """Lanzada cuando una o más métricas de Core Web Vitals superan el presupuesto permitido."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B5-12 (Inmutables)
# ---------------------------------------------------------------------------
class WebVitalsMetricDTO(BaseModel):
    """Métricas individuales de Core Web Vitals para una vista auditada."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    view_name: str
    ttfb_ms: float
    fcp_ms: float
    lcp_ms: float
    cls: float
    dom_interactive_ms: float
    passed: bool
    violations: list[str] = Field(default_factory=list)


class PerformanceReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la auditoría de rendimiento Web Vitals."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"perf-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_views_tested: int
    metrics: list[WebVitalsMetricDTO] = Field(default_factory=list)
    is_conforming: bool


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class PerformanceReporter:
    """Procesa métricas de Web Vitals, valida presupuestos y genera reportes inmutables."""

    @staticmethod
    def validate_metric(data: dict[str, Any] | WebVitalsMetricDTO) -> WebVitalsMetricDTO:
        """Valida una métrica contra los presupuestos y determina si cumple el estándar."""
        if isinstance(data, WebVitalsMetricDTO):
            view_name = data.view_name
            ttfb_ms = data.ttfb_ms
            fcp_ms = data.fcp_ms
            lcp_ms = data.lcp_ms
            cls = data.cls
            dom_interactive_ms = data.dom_interactive_ms
        elif isinstance(data, dict):
            view_name = str(data.get("view_name", "unknown_view"))
            ttfb_ms = float(data.get("ttfb_ms", 0.0))
            fcp_ms = float(data.get("fcp_ms", 0.0))
            lcp_ms = float(data.get("lcp_ms", 0.0))
            cls = float(data.get("cls", 0.0))
            dom_interactive_ms = float(data.get("dom_interactive_ms", 0.0))
        else:
            msg = f"Formato inválido para métrica de Web Vitals: {type(data)}"
            raise TypeError(msg)

        violations: list[str] = []
        if ttfb_ms > MAX_TTFB_MS:
            violations.append(f"TTFB excesivo: {ttfb_ms:.2f}ms > umbral {MAX_TTFB_MS:.2f}ms")
        if fcp_ms >= MAX_FCP_MS:
            violations.append(f"FCP excesivo: {fcp_ms:.2f}ms >= umbral {MAX_FCP_MS:.2f}ms")
        if lcp_ms > MAX_LCP_MS:
            violations.append(f"LCP excesivo: {lcp_ms:.2f}ms > umbral {MAX_LCP_MS:.2f}ms")
        if cls > MAX_CLS:
            violations.append(f"CLS excesivo: {cls:.4f} > umbral {MAX_CLS:.4f}")

        passed = len(violations) == 0

        return WebVitalsMetricDTO(
            view_name=view_name,
            ttfb_ms=round(ttfb_ms, 2),
            fcp_ms=round(fcp_ms, 2),
            lcp_ms=round(lcp_ms, 2),
            cls=round(cls, 4),
            dom_interactive_ms=round(dom_interactive_ms, 2),
            passed=passed,
            violations=violations,
        )

    @staticmethod
    def parse_playwright_results(
        results_data: dict[str, Any] | list[Any],
    ) -> PerformanceReportDTO:
        """Parsea los resultados de Playwright a un DTO inmutable PerformanceReportDTO."""
        raw_items: list[Any]
        if isinstance(results_data, dict):
            raw_items = results_data.get("metrics") or results_data.get("results") or []
        elif isinstance(results_data, list):
            raw_items = results_data
        else:
            msg = f"Tipo de dato no soportado para resultados de rendimiento: {type(results_data)}"
            raise TypeError(msg)

        validated_metrics: list[WebVitalsMetricDTO] = []
        for item in raw_items:
            validated_metrics.append(PerformanceReporter.validate_metric(item))

        total = len(validated_metrics)
        is_conforming = total > 0 and all(m.passed for m in validated_metrics)

        return PerformanceReportDTO(
            total_views_tested=total,
            metrics=validated_metrics,
            is_conforming=is_conforming,
        )

    @staticmethod
    def assert_zero_budget_violations(report: PerformanceReportDTO) -> None:
        """Verifica que no existan violaciones de presupuesto en ninguna vista auditada.

        Lanza PerformanceBudgetExceededError si el reporte no es conforme o contiene desvíos.
        """
        failing_metrics = [m for m in report.metrics if not m.passed or m.violations]

        if report.is_conforming and not failing_metrics:
            logger.info(
                "performance.audit_passed",
                report_id=report.report_id,
                total_views=report.total_views_tested,
            )
            return

        detalles_fallos: list[str] = []
        for m in failing_metrics:
            for v in m.violations:
                detalles_fallos.append(f"- Vista '{m.view_name}': {v}")

        mensaje = (
            f"Se detectaron violaciones de presupuesto de rendimiento en {len(failing_metrics)} "
            f"de {report.total_views_tested} vistas auditadas:\n" + "\n".join(detalles_fallos)
        )
        logger.error(
            "performance.audit_failed",
            report_id=report.report_id,
            failing_views=len(failing_metrics),
            total_views=report.total_views_tested,
        )
        raise PerformanceBudgetExceededError(mensaje)

    @staticmethod
    def export_report_json(
        report: PerformanceReportDTO,
        output_path: str | Path,
    ) -> Path:
        """Exporta el reporte estructurado en formato JSON determinista para EvidenceSealer."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = report.model_dump()
        target_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "performance.report_exported",
            report_id=report.report_id,
            path=str(target_path),
            is_conforming=report.is_conforming,
        )
        return target_path

    @staticmethod
    def load_report_json(report_path: str | Path) -> PerformanceReportDTO:
        """Carga y valida un reporte JSON de auditoría de rendimiento desde el disco."""
        source_path = Path(report_path).resolve()
        if not source_path.exists():
            msg = f"El archivo de reporte no existe: {source_path}"
            raise FileNotFoundError(msg)

        content = source_path.read_text(encoding="utf-8")
        return PerformanceReportDTO.model_validate_json(content)


# ---------------------------------------------------------------------------
# Entrada CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para validación de reportes de Core Web Vitals."""
    parser = argparse.ArgumentParser(
        description="Auditor y Validador de Core Web Vitals (KC-QA-B5-12)",
    )
    parser.add_argument(
        "--report-file",
        "-r",
        required=True,
        help="Ruta al archivo JSON de reporte de Web Vitals generado por Playwright",
    )
    parser.add_argument(
        "--check-budget",
        "--check-failures",
        dest="check_budget",
        action="store_true",
        help="Verifica compuerta estricta: retorna código de salida 1 si existen violaciones",
    )

    args = parser.parse_args(argv)

    try:
        report = PerformanceReporter.load_report_json(args.report_file)
        sys.stdout.write(
            f"Report ID: {report.report_id} | Total Vistas: {report.total_views_tested} | "
            f"Conforme: {report.is_conforming}\n"
        )

        if args.check_budget:
            PerformanceReporter.assert_zero_budget_violations(report)

        return 0
    except PerformanceBudgetExceededError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
