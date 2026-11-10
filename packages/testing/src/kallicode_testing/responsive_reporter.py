"""Módulo de Análisis y Reporte de Pruebas Responsivas UI (Desktop, Tablet, Mobile).

Ticket: KC-QA-B5-09
Épica: EQA4 / EQA5 - Motor de Pruebas Autónomo y E2E
Propósito: Evaluar, procesar y certificar la adaptabilidad responsiva del Portal Cliente
en los tres factores de forma canónicos (Desktop 1920x1080, Tablet 768x1024, Mobile 375x812),
garantizando cero desbordamiento horizontal involuntario (scrollWidth <= innerWidth),
colapso de navegación y operabilidad de flujos críticos en pantallas reducidas.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class ResponsiveLayoutError(RuntimeError):
    """Lanzada cuando se detectan fallos de layout responsivo o desbordamiento horizontal."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B5-09 (Inmutables)
# ---------------------------------------------------------------------------
class ViewportResultDTO(BaseModel):
    """Resultado de auditoría responsiva para una vista y un viewport específico."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    view_name: str
    viewport_label: Literal["desktop", "tablet", "mobile"]
    width: int
    height: int
    has_horizontal_overflow: bool
    sidebar_collapsed: bool
    passed: bool
    error_message: str | None = None


class ResponsiveAuditReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la auditoría responsiva multi-dispositivo."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"resp-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_scenarios: int
    failed_scenarios: int
    results: list[ViewportResultDTO] = Field(default_factory=list)
    is_conforming: bool


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class ResponsiveReporter:
    """Procesa resultados de pruebas responsivas, valida compuertas y genera reportes."""

    @staticmethod
    def parse_playwright_results(
        results_data: list[dict[str, Any]] | list[ViewportResultDTO],
    ) -> ResponsiveAuditReportDTO:
        """Parsea una lista de resultados de Playwright a un DTO inmutable ResponsiveAuditReportDTO."""
        parsed_results: list[ViewportResultDTO] = []

        for item in results_data:
            if isinstance(item, ViewportResultDTO):
                parsed_results.append(item)
            elif isinstance(item, dict):
                # Determinar si el escenario pasó considerando desbordamiento
                has_overflow = bool(item.get("has_horizontal_overflow", False))
                explicit_passed = bool(item.get("passed", True))
                scenario_passed = explicit_passed and not has_overflow

                raw_label = item.get("viewport_label", "desktop")
                viewport_label: Literal["desktop", "tablet", "mobile"] = (
                    raw_label if raw_label in ("desktop", "tablet", "mobile") else "desktop"
                )

                dto = ViewportResultDTO(
                    view_name=str(item.get("view_name", "unknown_view")),
                    viewport_label=viewport_label,
                    width=int(item.get("width", 0)),
                    height=int(item.get("height", 0)),
                    has_horizontal_overflow=has_overflow,
                    sidebar_collapsed=bool(item.get("sidebar_collapsed", False)),
                    passed=scenario_passed,
                    error_message=item.get("error_message"),
                )
                parsed_results.append(dto)
            else:
                msg = f"Tipo de dato no soportado para resultado de viewport: {type(item)}"
                raise TypeError(msg)

        total = len(parsed_results)
        failed = sum(1 for r in parsed_results if (not r.passed or r.has_horizontal_overflow))
        is_conforming = total > 0 and failed == 0

        return ResponsiveAuditReportDTO(
            total_scenarios=total,
            failed_scenarios=failed,
            results=parsed_results,
            is_conforming=is_conforming,
        )

    @staticmethod
    def assert_no_responsive_failures(report: ResponsiveAuditReportDTO) -> None:
        """Verifica que no existan fallos de layout ni desbordamientos horizontales.

        Lanza ResponsiveLayoutError si el reporte no es conforme o contiene escenarios fallidos.
        """
        if report.is_conforming and report.failed_scenarios == 0:
            logger.info(
                "responsive.audit_passed",
                report_id=report.report_id,
                total_scenarios=report.total_scenarios,
            )
            return

        detalles_fallos: list[str] = []
        for r in report.results:
            if not r.passed or r.has_horizontal_overflow:
                razon = []
                if r.has_horizontal_overflow:
                    razon.append(
                        "Desbordamiento horizontal involuntario detectado (scrollWidth > innerWidth)"
                    )
                if r.error_message:
                    razon.append(f"Error: {r.error_message}")
                if not razon:
                    razon.append("Estado no conforme")

                detalles_fallos.append(
                    f"- Vista '{r.view_name}' ({r.viewport_label} {r.width}x{r.height}): {'; '.join(razon)}"
                )

        mensaje = (
            f"Se detectaron {report.failed_scenarios} escenarios responsivos no conformes "
            f"de un total de {report.total_scenarios}:\n" + "\n".join(detalles_fallos)
        )
        logger.error(
            "responsive.audit_failed",
            report_id=report.report_id,
            failed_scenarios=report.failed_scenarios,
            total_scenarios=report.total_scenarios,
        )
        raise ResponsiveLayoutError(mensaje)

    @staticmethod
    def export_report_json(
        report: ResponsiveAuditReportDTO,
        output_path: str | Path,
    ) -> Path:
        """Exporta el reporte estructurado en formato JSON legible y determinista."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = report.model_dump()
        target_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "responsive.report_exported",
            report_id=report.report_id,
            path=str(target_path),
            is_conforming=report.is_conforming,
        )
        return target_path

    @staticmethod
    def load_report_json(report_path: str | Path) -> ResponsiveAuditReportDTO:
        """Carga y valida un reporte JSON de auditoría responsiva desde el disco."""
        source_path = Path(report_path).resolve()
        if not source_path.exists():
            msg = f"El archivo de reporte no existe: {source_path}"
            raise FileNotFoundError(msg)

        content = source_path.read_text(encoding="utf-8")
        return ResponsiveAuditReportDTO.model_validate_json(content)


# ---------------------------------------------------------------------------
# Entrada CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para verificación de reportes responsivos."""
    parser = argparse.ArgumentParser(
        description="Validador y Auditor Responsivo de UI (KC-QA-B5-09)",
    )
    parser.add_argument(
        "--report-file",
        "-r",
        required=True,
        help="Ruta al archivo JSON de reporte responsivo generado por Playwright",
    )
    parser.add_argument(
        "--check-failures",
        action="store_true",
        help="Verifica compuerta estricta: retorna código de salida 1 si existen fallos",
    )

    args = parser.parse_args(argv)

    try:
        report = ResponsiveReporter.load_report_json(args.report_file)
        sys.stdout.write(
            f"Report ID: {report.report_id} | Total: {report.total_scenarios} | "
            f"Fallidos: {report.failed_scenarios} | Conforme: {report.is_conforming}\n"
        )

        if args.check_failures:
            ResponsiveReporter.assert_no_responsive_failures(report)

        return 0
    except ResponsiveLayoutError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
