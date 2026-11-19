"""Analizador y Reportero de Pruebas de Estrés y Punto de Ruptura k6 (KC-QA-B6-02).

Ticket: KC-QA-B6-02
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Propósito: Parsear `stress-summary.json`, identificar `max_stable_rps` y `breaking_point_vus`,
certificar la recuperación automática en fase cooldown (`p95 < 300ms`, 0% errores sin reinicio)
y exportar reportes Pydantic v2 inmutables (`frozen=True`) listos para sellado WORM.
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

# Umbrales de Ruptura y Recuperación Post-Estrés
RECOVERY_MAX_P95_MS: float = 300.0
RECOVERY_MAX_ERROR_RATE: float = 0.0
BREAKING_POINT_P95_MS: float = 1000.0
BREAKING_POINT_P99_MS: float = 2000.0
BREAKING_POINT_ERROR_RATE: float = 0.05

_CANONICAL_STAGES: tuple[tuple[str, int, float], ...] = (
    ("stage_20_vus", 20, 60.0),
    ("stage_50_vus", 50, 60.0),
    ("stage_100_vus", 100, 120.0),
    ("stage_200_vus", 200, 120.0),
    ("stage_300_vus", 300, 60.0),
    ("cooldown", 0, 60.0),
)


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class StressRecoveryFailedError(RuntimeError):
    """Lanzada si el backend permanece degradado o con errores tras el estrés (cooldown)."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-02 (Inmutables)
# ---------------------------------------------------------------------------
class StressStageMetricDTO(BaseModel):
    """Métricas inmutables observadas para una etapa de la rampa de estrés."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    stage_name: str
    vus_target: int
    rps_achieved: float
    p95_ms: float
    p99_ms: float
    error_rate: float
    stage_passed: bool


class StressTestReportDTO(BaseModel):
    """Reporte consolidado e inmutable de estrés, punto de ruptura y recuperación."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"stress-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    max_stable_rps: float
    breaking_point_vus: int | None = None
    recovered_successfully: bool
    stages: list[StressStageMetricDTO] = Field(default_factory=list)
    is_conforming: bool


# ---------------------------------------------------------------------------
# Helpers internos de extracción y evaluación
# ---------------------------------------------------------------------------
def _extract_values(metric_block: Any) -> dict[str, Any]:
    if isinstance(metric_block, dict):
        values = metric_block.get("values")
        if isinstance(values, dict):
            return values
        return metric_block
    return {}


def _is_recovery_stage(stage_name: str) -> bool:
    lowered = stage_name.lower()
    return "cooldown" in lowered or "recovery" in lowered


def _evaluate_stage_passed(
    stage_name: str,
    p95_ms: float,
    p99_ms: float,
    error_rate: float,
) -> bool:
    if _is_recovery_stage(stage_name):
        return p95_ms < RECOVERY_MAX_P95_MS and error_rate <= RECOVERY_MAX_ERROR_RATE
    return (
        error_rate <= BREAKING_POINT_ERROR_RATE
        and p95_ms < BREAKING_POINT_P95_MS
        and p99_ms < BREAKING_POINT_P99_MS
    )


def _stage_metric_keys(stage_name: str) -> tuple[str, str, str]:
    if stage_name == "cooldown":
        return (
            "stress_cooldown_duration",
            "stress_cooldown_errors",
            "stress_cooldown_reqs",
        )
    return (
        f"stress_{stage_name}_duration",
        f"stress_{stage_name}_errors",
        f"stress_{stage_name}_reqs",
    )


def _build_stage_plan(summary_data: dict[str, Any]) -> list[tuple[str, int, float]]:
    metadata = summary_data.get("kallicode_stress_metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("stages"), list):
        plan: list[tuple[str, int, float]] = []
        for entry in metadata["stages"]:
            if isinstance(entry, dict) and entry.get("name"):
                plan.append(
                    (
                        str(entry["name"]),
                        int(entry.get("vus_target", 0)),
                        float(entry.get("duration_seconds", 60.0)),
                    )
                )
        if plan:
            return plan
    return list(_CANONICAL_STAGES)


def _evaluate_recovery_from_metrics(
    summary_data: dict[str, Any],
    metrics_dict: dict[str, Any],
    stages: list[StressStageMetricDTO],
) -> bool:
    # 1. Métricas explícitas de sondeo post-estrés en teardown()
    if "recovery_req_duration" in metrics_dict:
        rec_dur = _extract_values(metrics_dict["recovery_req_duration"])
        rec_err = _extract_values(metrics_dict.get("recovery_error_rate"))
        p95_rec = float(rec_dur.get("p(95)", 0.0))
        err_rec = float(rec_err.get("rate", 0.0))
        count_rec = float(rec_dur.get("count", 1.0))
        return (
            count_rec > 0.0 and p95_rec < RECOVERY_MAX_P95_MS and err_rec <= RECOVERY_MAX_ERROR_RATE
        )

    # 2. Etapa cooldown/recovery dentro de stages
    recovery_stages = [s for s in stages if _is_recovery_stage(s.stage_name)]
    if recovery_stages:
        return all(
            s.stage_passed
            and s.p95_ms < RECOVERY_MAX_P95_MS
            and s.error_rate <= RECOVERY_MAX_ERROR_RATE
            for s in recovery_stages
        )

    # 3. Bandera explícita si no existen métricas granulares de recuperación
    if isinstance(summary_data.get("recovered_successfully"), bool):
        return bool(summary_data["recovered_successfully"])

    return False


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class StressReporter:
    """Analiza pruebas de estrés k6, detecta punto de ruptura y certifica cooldown."""

    @staticmethod
    def parse_k6_summary(summary_data: dict[str, Any]) -> StressTestReportDTO:
        """Parsea `stress-summary.json` hacia un DTO inmutable `StressTestReportDTO`."""
        if not isinstance(summary_data, dict):
            msg = f"Se esperaba dict para summary_data, recibido: {type(summary_data)}"
            raise TypeError(msg)

        parsed_stages: list[StressStageMetricDTO] = []
        raw_metrics = summary_data.get("metrics")
        metrics_dict = raw_metrics if isinstance(raw_metrics, dict) else {}

        state_block = summary_data.get("state")
        total_duration_s = (
            float(state_block["testRunDurationMs"]) / 1000.0
            if isinstance(state_block, dict) and "testRunDurationMs" in state_block
            else float(summary_data.get("duration_seconds", 0.0))
        )

        # Ruta A: lista explícita de etapas en payload estructurado
        if isinstance(summary_data.get("stages"), list):
            for item in summary_data["stages"]:
                if isinstance(item, StressStageMetricDTO):
                    stage_name = item.stage_name
                    vus_target = item.vus_target
                    rps = item.rps_achieved
                    p95 = item.p95_ms
                    p99 = item.p99_ms
                    err = item.error_rate
                elif isinstance(item, dict):
                    stage_name = str(item.get("stage_name", "unknown"))
                    vus_target = int(item.get("vus_target", 0))
                    rps = float(item.get("rps_achieved", 0.0))
                    p95 = float(item.get("p95_ms", 0.0))
                    p99 = float(item.get("p99_ms", 0.0))
                    err = float(item.get("error_rate", 0.0))
                else:
                    msg = f"Tipo inválido en lista stages: {type(item)}"
                    raise TypeError(msg)

                passed = _evaluate_stage_passed(stage_name, p95, p99, err)
                parsed_stages.append(
                    StressStageMetricDTO(
                        stage_name=stage_name,
                        vus_target=vus_target,
                        rps_achieved=round(rps, 2),
                        p95_ms=round(p95, 3),
                        p99_ms=round(p99, 3),
                        error_rate=round(err, 6),
                        stage_passed=passed,
                    )
                )

        # Ruta B: resumen nativo k6 con métricas por etapa o etiquetas
        elif metrics_dict:
            stage_plan = _build_stage_plan(summary_data)
            for stage_name, vus_target, planned_dur_s in stage_plan:
                dur_key, err_key, reqs_key = _stage_metric_keys(stage_name)
                tag_dur_key = f"http_req_duration{{stage:{stage_name}}}"
                tag_err_key = f"http_req_failed{{stage:{stage_name}}}"
                tag_reqs_key = f"http_reqs{{stage:{stage_name}}}"

                dur_values = _extract_values(
                    metrics_dict.get(dur_key) or metrics_dict.get(tag_dur_key)
                )
                if not dur_values:
                    continue

                err_values = _extract_values(
                    metrics_dict.get(err_key) or metrics_dict.get(tag_err_key)
                )
                reqs_values = _extract_values(
                    metrics_dict.get(reqs_key) or metrics_dict.get(tag_reqs_key)
                )

                p95 = float(dur_values.get("p(95)", 0.0))
                p99 = float(dur_values.get("p(99)", 0.0))
                err = float(err_values.get("rate", 0.0))

                req_count = float(reqs_values.get("count", dur_values.get("count", 0.0)))
                if "rate" in reqs_values:
                    rps = float(reqs_values["rate"])
                else:
                    effective_dur = (
                        min(planned_dur_s, total_duration_s)
                        if total_duration_s > 0.0
                        else planned_dur_s
                    )
                    rps = req_count / effective_dur if effective_dur > 0.0 else 0.0

                passed = _evaluate_stage_passed(stage_name, p95, p99, err)
                parsed_stages.append(
                    StressStageMetricDTO(
                        stage_name=stage_name,
                        vus_target=vus_target,
                        rps_achieved=round(rps, 2),
                        p95_ms=round(p95, 3),
                        p99_ms=round(p99, 3),
                        error_rate=round(err, 6),
                        stage_passed=passed,
                    )
                )

            # Fallback sintético si el run CLI usó métricas globales sin etapas nombradas
            if not parsed_stages and "http_req_duration" in metrics_dict:
                global_dur = _extract_values(metrics_dict["http_req_duration"])
                if global_dur:
                    vus_vals = _extract_values(metrics_dict.get("vus_max"))
                    reqs_vals = _extract_values(metrics_dict.get("http_reqs"))
                    err_vals = _extract_values(metrics_dict.get("http_req_failed"))
                    p95 = float(global_dur.get("p(95)", 0.0))
                    p99 = float(global_dur.get("p(99)", 0.0))
                    err = float(err_vals.get("rate", 0.0))
                    rps = float(reqs_vals.get("rate", 0.0))
                    vus_max = int(vus_vals.get("max", 0))
                    passed = _evaluate_stage_passed("stress_global", p95, p99, err)
                    parsed_stages.append(
                        StressStageMetricDTO(
                            stage_name="stress_global",
                            vus_target=vus_max,
                            rps_achieved=round(rps, 2),
                            p95_ms=round(p95, 3),
                            p99_ms=round(p99, 3),
                            error_rate=round(err, 6),
                            stage_passed=passed,
                        )
                    )

        # Identificar max_stable_rps y breaking_point_vus en etapas de carga (no cooldown)
        max_stable_rps = 0.0
        breaking_point_vus: int | None = None
        for stage in parsed_stages:
            if _is_recovery_stage(stage.stage_name):
                continue
            if breaking_point_vus is None and stage.stage_passed:
                max_stable_rps = max(max_stable_rps, stage.rps_achieved)
            elif breaking_point_vus is None and not stage.stage_passed:
                breaking_point_vus = stage.vus_target

        recovered_successfully = _evaluate_recovery_from_metrics(
            summary_data,
            metrics_dict,
            parsed_stages,
        )

        # Si hay métricas de sondeo en teardown() pero no se agregó etapa cooldown, anexar resumen
        if "recovery_req_duration" in metrics_dict and not any(
            _is_recovery_stage(s.stage_name) for s in parsed_stages
        ):
            rec_dur = _extract_values(metrics_dict["recovery_req_duration"])
            rec_err = _extract_values(metrics_dict.get("recovery_error_rate"))
            rec_reqs = _extract_values(metrics_dict.get("recovery_reqs_count"))
            rec_p95 = round(float(rec_dur.get("p(95)", 0.0)), 3)
            rec_p99 = round(float(rec_dur.get("p(99)", 0.0)), 3)
            rec_rate = round(float(rec_err.get("rate", 0.0)), 6)
            rec_count = float(rec_reqs.get("count", rec_dur.get("count", 0.0)))
            parsed_stages.append(
                StressStageMetricDTO(
                    stage_name="cooldown_recovery",
                    vus_target=0,
                    rps_achieved=round(rec_count, 2),
                    p95_ms=rec_p95,
                    p99_ms=rec_p99,
                    error_rate=rec_rate,
                    stage_passed=_evaluate_stage_passed(
                        "cooldown_recovery", rec_p95, rec_p99, rec_rate
                    ),
                )
            )

        is_conforming = len(parsed_stages) > 0 and recovered_successfully

        return StressTestReportDTO(
            max_stable_rps=round(max_stable_rps, 2),
            breaking_point_vus=breaking_point_vus,
            recovered_successfully=recovered_successfully,
            stages=parsed_stages,
            is_conforming=is_conforming,
        )

    @staticmethod
    def assert_system_recovered(report: StressTestReportDTO) -> None:
        """Certifica que el sistema recuperó operación normal en cooldown (< 300ms p95)."""
        if not report.stages:
            logger.error("stress_test.empty_stages", report_id=report.report_id)
            raise StressRecoveryFailedError(
                "El reporte de estrés no contiene etapas evaluadas (prohibido falso verde)."
            )

        recovery_failures: list[str] = []
        for stage in report.stages:
            if _is_recovery_stage(stage.stage_name):
                if stage.p95_ms >= RECOVERY_MAX_P95_MS:
                    recovery_failures.append(
                        f"Etapa '{stage.stage_name}': p95={stage.p95_ms:.2f}ms >= "
                        f"umbral {RECOVERY_MAX_P95_MS:.2f}ms"
                    )
                if stage.error_rate > RECOVERY_MAX_ERROR_RATE:
                    recovery_failures.append(
                        f"Etapa '{stage.stage_name}': error_rate={stage.error_rate:.4f} > 0.0"
                    )

        if not report.recovered_successfully or not report.is_conforming or recovery_failures:
            if not recovery_failures:
                recovery_failures.append(
                    "recovered_successfully=False o is_conforming=False tras fase cooldown"
                )
            message = (
                f"El backend no recuperó su estabilidad post-estrés en {report.report_id}:\n- "
                + "\n- ".join(recovery_failures)
            )
            logger.error(
                "stress_test.recovery_failed",
                report_id=report.report_id,
                breaking_point_vus=report.breaking_point_vus,
                max_stable_rps=report.max_stable_rps,
            )
            raise StressRecoveryFailedError(message)

        logger.info(
            "stress_test.recovery_verified",
            report_id=report.report_id,
            max_stable_rps=report.max_stable_rps,
            breaking_point_vus=report.breaking_point_vus,
            stages_count=len(report.stages),
        )

    @staticmethod
    def export_report_json(report: StressTestReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte estructurado e inmutable a JSON determinista para sellado WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "stress_test.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para procesar `stress-summary.json` y certificar recuperación."""
    parser = argparse.ArgumentParser(
        description="Analizador de Estrés y Punto de Ruptura k6 (KC-QA-B6-02)",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=Path("stress-summary.json"),
        help="Ruta al archivo JSON generado por k6_stress_break.js.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("stress-break-report.json"),
        help="Ruta de salida para el reporte DTO normalizado (WORM-ready).",
    )
    parser.add_argument(
        "--check-recovery",
        action="store_true",
        help="Retorna exit code 1 si el backend no se recuperó tras el estrés.",
    )
    args = parser.parse_args(argv)

    if not args.summary_file.exists():
        logger.error("stress_test.summary_not_found", path=str(args.summary_file))
        return 1

    raw_data = json.loads(args.summary_file.read_text(encoding="utf-8"))
    report = StressReporter.parse_k6_summary(raw_data)
    StressReporter.export_report_json(report, args.output_file)

    if args.check_recovery:
        try:
            StressReporter.assert_system_recovered(report)
        except StressRecoveryFailedError:
            return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
