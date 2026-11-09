"""Módulo de Análisis y Reporte de Resiliencia del Frontend (Zero WSOD y Tolerancia a Fallos).

Ticket: KC-QA-B5-10
Épica: EQA4 / EQA5 - Motor de Pruebas Autónomo y E2E
Propósito: Evaluar, procesar y certificar la resiliencia del Portal Cliente ante caídas
catastróficas o transitorias de red y backend (connection_refused, timeout, HTTP 500/503),
garantizando cero pantallas blancas (Zero WSOD), activación contenida de límites de error,
mensajes amigables y recuperación automática de datos tras reconexión sin pérdida de sesión.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

TipoFalloResiliencia = Literal[
    "connection_refused",
    "timeout",
    "http_500",
    "http_503",
    "flaky_reconnection",
]

FALLOS_VALIDOS: set[str] = {
    "connection_refused",
    "timeout",
    "http_500",
    "http_503",
    "flaky_reconnection",
}


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class FrontendResilienceError(RuntimeError):
    """Lanzada cuando se detectan fallos de resiliencia del frontend o pantallas blancas."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B5-10 (Inmutables)
# ---------------------------------------------------------------------------
class ResilienceScenarioResultDTO(BaseModel):
    """Resultado individual de un escenario de prueba de resiliencia del frontend."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    description: str
    failure_type: TipoFalloResiliencia
    error_boundary_triggered: bool
    white_screen_detected: bool
    recovered_successfully: bool
    passed: bool
    error_message: str | None = None


class FrontendResilienceReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la auditoría de resiliencia del frontend."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"resil-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_scenarios: int
    passed_scenarios: int
    scenarios: list[ResilienceScenarioResultDTO] = Field(default_factory=list)
    is_conforming: bool


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class ResilienceReporter:
    """Procesa resultados de pruebas de resiliencia, valida compuertas y genera reportes."""

    @staticmethod
    def parse_playwright_results(
        results_data: dict[str, Any] | list[Any],
    ) -> FrontendResilienceReportDTO:
        """Parsea resultados de Playwright a un DTO inmutable FrontendResilienceReportDTO."""
        raw_items: list[Any]
        if isinstance(results_data, dict):
            raw_items = results_data.get("scenarios") or results_data.get("results") or []
        elif isinstance(results_data, list):
            raw_items = results_data
        else:
            msg = f"Formato no soportado para resultados de resiliencia: {type(results_data)}"
            raise TypeError(msg)

        parsed_scenarios: list[ResilienceScenarioResultDTO] = []

        for item in raw_items:
            if isinstance(item, ResilienceScenarioResultDTO):
                parsed_scenarios.append(item)
            elif isinstance(item, dict):
                wsod = bool(item.get("white_screen_detected", False))
                recovered = bool(item.get("recovered_successfully", True))
                explicit_passed = bool(item.get("passed", True))

                # Regla de oro de resiliencia: WSOD invalida inmediatamente el escenario
                scenario_passed = explicit_passed and not wsod and recovered

                raw_ft = str(item.get("failure_type", "connection_refused"))
                failure_type: TipoFalloResiliencia = (
                    cast(TipoFalloResiliencia, raw_ft)
                    if raw_ft in FALLOS_VALIDOS
                    else "connection_refused"
                )

                dto = ResilienceScenarioResultDTO(
                    scenario_id=str(item.get("scenario_id", f"scen-{len(parsed_scenarios) + 1}")),
                    description=str(item.get("description", "Escenario de resiliencia")),
                    failure_type=failure_type,
                    error_boundary_triggered=bool(item.get("error_boundary_triggered", False)),
                    white_screen_detected=wsod,
                    recovered_successfully=recovered,
                    passed=scenario_passed,
                    error_message=item.get("error_message"),
                )
                parsed_scenarios.append(dto)
            else:
                msg = f"Elemento inválido en lista de escenarios: {type(item)}"
                raise TypeError(msg)

        total = len(parsed_scenarios)
        passed = sum(1 for s in parsed_scenarios if s.passed and not s.white_screen_detected)
        is_conforming = total > 0 and passed == total

        return FrontendResilienceReportDTO(
            total_scenarios=total,
            passed_scenarios=passed,
            scenarios=parsed_scenarios,
            is_conforming=is_conforming,
        )

    @staticmethod
    def assert_zero_resilience_failures(report: FrontendResilienceReportDTO) -> None:
        """Verifica que no existan fallos de resiliencia ni pantallas blancas (Zero WSOD).

        Lanza FrontendResilienceError si el reporte no es conforme o contiene escenarios fallidos.
        """
        wsod_cases = [s for s in report.scenarios if s.white_screen_detected]
        failed_cases = [s for s in report.scenarios if not s.passed or s.white_screen_detected]

        if report.is_conforming and not failed_cases:
            logger.info(
                "resilience.audit_passed",
                report_id=report.report_id,
                total_scenarios=report.total_scenarios,
                passed_scenarios=report.passed_scenarios,
            )
            return

        detalles_fallos: list[str] = []
        if wsod_cases:
            for s in wsod_cases:
                detalles_fallos.append(
                    f"[CRITICAL WSOD] Escenario '{s.scenario_id}' ({s.description}): "
                    f"Pantalla blanca detectada ante fallo '{s.failure_type}'."
                )

        for s in failed_cases:
            if s not in wsod_cases:
                razon = s.error_message or "Fallo en recuperación o aserción de resiliencia"
                detalles_fallos.append(
                    f"[FAILED] Escenario '{s.scenario_id}' ({s.description}): {razon}"
                )

        mensaje = (
            f"Se detectaron fallos de resiliencia en el frontend ({len(failed_cases)} no conformes "
            f"de {report.total_scenarios}):\n" + "\n".join(detalles_fallos)
        )
        logger.error(
            "resilience.audit_failed",
            report_id=report.report_id,
            total_scenarios=report.total_scenarios,
            passed_scenarios=report.passed_scenarios,
            failed_count=len(failed_cases),
            wsod_count=len(wsod_cases),
        )
        raise FrontendResilienceError(mensaje)

    @staticmethod
    def export_report_json(
        report: FrontendResilienceReportDTO,
        output_path: str | Path,
    ) -> Path:
        """Exporta el reporte estructurado en formato JSON legible y determinista para EvidenceSealer."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = report.model_dump()
        target_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "resilience.report_exported",
            report_id=report.report_id,
            path=str(target_path),
            is_conforming=report.is_conforming,
        )
        return target_path

    @staticmethod
    def load_report_json(report_path: str | Path) -> FrontendResilienceReportDTO:
        """Carga y valida un reporte JSON de auditoría de resiliencia desde el disco."""
        source_path = Path(report_path).resolve()
        if not source_path.exists():
            msg = f"El archivo de reporte no existe: {source_path}"
            raise FileNotFoundError(msg)

        content = source_path.read_text(encoding="utf-8")
        return FrontendResilienceReportDTO.model_validate_json(content)


# ---------------------------------------------------------------------------
# Entrada CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para validación de reportes de resiliencia."""
    parser = argparse.ArgumentParser(
        description="Auditor y Validador de Resiliencia del Frontend (KC-QA-B5-10)",
    )
    parser.add_argument(
        "--report-file",
        "-r",
        required=True,
        help="Ruta al archivo JSON de reporte de resiliencia generado por Playwright",
    )
    parser.add_argument(
        "--check-failures",
        action="store_true",
        help="Verifica compuerta estricta: retorna código de salida 1 si existen fallos o WSOD",
    )

    args = parser.parse_args(argv)

    try:
        report = ResilienceReporter.load_report_json(args.report_file)
        sys.stdout.write(
            f"Report ID: {report.report_id} | Total: {report.total_scenarios} | "
            f"Exitosos: {report.passed_scenarios} | Conforme: {report.is_conforming}\n"
        )

        if args.check_failures:
            ResilienceReporter.assert_zero_resilience_failures(report)

        return 0
    except FrontendResilienceError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
