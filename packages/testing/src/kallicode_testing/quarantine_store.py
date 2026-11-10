"""Gestor de Cuarentena de Tests Inestables (Flaky Tests Quarantine).

Ticket: KC-QA-B4-04 (Cierra hallazgo QA-F09)
Épica: EQA7 - Motor de Pruebas Autónomo y E2E
Propósito: Aislar pruebas inestables para evitar falsos verdes que aprueben el gate en silencio,
excluyéndolas de la suite bloqueante sin perder señal ni métricas, imponiendo compuerta dura
de tasa máxima de inestabilidad (Flakiness Rate Gate) y exigiendo disciplina de entrada
(ticket de desflake + motivo) y graduación (2 ejecuciones consecutivas PASS).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

QuarantineStatus = Literal["active", "probation", "graduated", "retired"]
ExecutionResult = Literal["pass", "fail", "flaky_pass"]

DEFAULT_FLAKINESS_THRESHOLD: float = 0.02  # 2.0%
UNSTABLE_STREAK_THRESHOLD: int = 3
CONSECUTIVE_PASSES_TO_GRADUATE: int = 2


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class FlakinessThresholdExceededError(Exception):
    """Lanzada cuando la tasa global de inestabilidad supera el umbral máximo permitido."""


class QuarantineValidationError(Exception):
    """Lanzada cuando faltan campos obligatorios (reason, desflake_ticket) al cuarentenar un test."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-04 (Inmutables)
# ---------------------------------------------------------------------------
class FlakyHistoryEntryDTO(BaseModel):
    """Registro inmutable de una ejecución individual de prueba."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    run_id: str
    executed_at: str
    result: ExecutionResult
    duration_ms: float
    error_excerpt: str | None = None


class QuarantineRecordDTO(BaseModel):
    """Ficha inmutable de cuarentena para un test específico dentro de un tenant."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    test_identifier: str
    criterio_id: str
    status: QuarantineStatus
    streak_count: int
    flaky_count: int
    reason: str
    desflake_ticket: str
    entered_at: str
    last_seen_at: str
    history: list[FlakyHistoryEntryDTO] = Field(default_factory=list)


class RunFlakinessSummaryDTO(BaseModel):
    """Resumen estructurado e inmutable de la tasa de inestabilidad de una corrida."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    run_id: str
    total_tests: int
    flaky_tests_count: int
    flakiness_rate: float
    threshold_max: float
    exceeded_threshold: bool
    quarantined_tests_active: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gestor de Cuarentena Principal
# ---------------------------------------------------------------------------
class QuarantineStore:
    """Administrador multi-tenant del ciclo de vida de cuarentena de pruebas inestables."""

    def __init__(self, observation_window: int = 20) -> None:
        self.observation_window = observation_window
        # Estructura multi-tenant: dict[tenant_id, dict[test_identifier, QuarantineRecordDTO]]
        self._store: dict[str, dict[str, QuarantineRecordDTO]] = {}
        # Contador auxiliar de pases consecutivos en cuarentena: dict[(tenant_id, test_id), int]
        self._consecutive_passes: dict[tuple[str, str], int] = {}

    def _get_tenant_records(self, tenant_id: str) -> dict[str, QuarantineRecordDTO]:
        if tenant_id not in self._store:
            self._store[tenant_id] = {}
        return self._store[tenant_id]

    def register_execution(
        self,
        tenant_id: str,
        test_id: str,
        criterio_id: str,
        result: ExecutionResult,
        error_excerpt: str | None = None,
        duration_ms: float = 0.0,
        run_id: str | None = None,
        reason: str | None = None,
        desflake_ticket: str | None = None,
    ) -> QuarantineRecordDTO:
        """Registra una ejecución de prueba y evalúa automáticamente transiciones de cuarentena."""
        tenant_records = self._get_tenant_records(tenant_id)
        now_iso = datetime.now(UTC).isoformat()
        current_run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"

        new_history_entry = FlakyHistoryEntryDTO(
            run_id=current_run_id,
            executed_at=now_iso,
            result=result,
            duration_ms=duration_ms,
            error_excerpt=error_excerpt,
        )

        existing = tenant_records.get(test_id)
        pass_key = (tenant_id, test_id)

        if existing is None:
            # Nuevo registro en observación
            is_unstable = result in ("fail", "flaky_pass")
            initial_streak = 1 if is_unstable else 0
            initial_flaky = 1 if is_unstable else 0
            record = QuarantineRecordDTO(
                tenant_id=tenant_id,
                test_identifier=test_id,
                criterio_id=criterio_id,
                status="retired",
                streak_count=initial_streak,
                flaky_count=initial_flaky,
                reason=reason
                or (
                    f"Falla intermitente detectada ({result})" if is_unstable else "En observación"
                ),
                desflake_ticket=desflake_ticket or f"DESFLAKE-{test_id.replace('::', '-')}",
                entered_at=now_iso,
                last_seen_at=now_iso,
                history=[new_history_entry],
            )
            tenant_records[test_id] = record
            return record

        # Registro existente: actualizar historial
        updated_history = list(existing.history)
        updated_history.append(new_history_entry)
        if len(updated_history) > self.observation_window:
            updated_history = updated_history[-self.observation_window :]

        streak_count = existing.streak_count
        flaky_count = existing.flaky_count
        status = existing.status
        entered_at = existing.entered_at

        if result in ("fail", "flaky_pass"):
            # Racha inestable
            streak_count += 1
            flaky_count += 1
            self._consecutive_passes[pass_key] = 0

            # Si falla estando en probatoria, regresa a cuarentena activa
            if status == "probation":
                status = "active"
                logger.warning(
                    "quarantine.probation_relapsed_to_active",
                    tenant_id=tenant_id,
                    test_id=test_id,
                )
            elif streak_count >= UNSTABLE_STREAK_THRESHOLD and status != "active":
                status = "active"
                entered_at = now_iso
                logger.warning(
                    "quarantine.auto_quarantine_triggered",
                    tenant_id=tenant_id,
                    test_id=test_id,
                    streak_count=streak_count,
                )
        else:
            # Resultado PASS
            if status in ("active", "probation"):
                current_passes = self._consecutive_passes.get(pass_key, 0) + 1
                self._consecutive_passes[pass_key] = current_passes

                if current_passes >= CONSECUTIVE_PASSES_TO_GRADUATE:
                    status = "graduated"
                    streak_count = 0
                    logger.info(
                        "quarantine.test_graduated",
                        tenant_id=tenant_id,
                        test_id=test_id,
                        consecutive_passes=current_passes,
                    )
                else:
                    status = "probation"
            else:
                self._consecutive_passes[pass_key] = 0

        updated_record = QuarantineRecordDTO(
            tenant_id=tenant_id,
            test_identifier=test_id,
            criterio_id=criterio_id or existing.criterio_id,
            status=status,
            streak_count=streak_count,
            flaky_count=flaky_count,
            reason=reason or existing.reason,
            desflake_ticket=desflake_ticket or existing.desflake_ticket,
            entered_at=entered_at,
            last_seen_at=now_iso,
            history=updated_history,
        )
        tenant_records[test_id] = updated_record
        return updated_record

    def quarantine_test(
        self,
        tenant_id: str,
        test_id: str,
        reason: str,
        desflake_ticket: str,
        criterio_id: str = "CRIT-UNKNOWN",
    ) -> QuarantineRecordDTO:
        """Pone explícitamente en cuarentena un test, exigiendo reason y desflake_ticket obligatorios."""
        if not reason or not reason.strip():
            msg = "No se puede cuarentenar un test sin motivo tipificado (reason)."
            raise QuarantineValidationError(msg)
        if not desflake_ticket or not desflake_ticket.strip():
            msg = (
                "No se puede cuarentenar un test sin ticket de desflake asociado (desflake_ticket)."
            )
            raise QuarantineValidationError(msg)

        tenant_records = self._get_tenant_records(tenant_id)
        now_iso = datetime.now(UTC).isoformat()
        existing = tenant_records.get(test_id)

        history = list(existing.history) if existing else []
        streak = existing.streak_count if existing else 3
        flaky = existing.flaky_count if existing else 1

        record = QuarantineRecordDTO(
            tenant_id=tenant_id,
            test_identifier=test_id,
            criterio_id=criterio_id if not existing else existing.criterio_id,
            status="active",
            streak_count=max(streak, UNSTABLE_STREAK_THRESHOLD),
            flaky_count=max(flaky, 1),
            reason=reason.strip(),
            desflake_ticket=desflake_ticket.strip(),
            entered_at=now_iso,
            last_seen_at=now_iso,
            history=history,
        )
        tenant_records[test_id] = record
        self._consecutive_passes[(tenant_id, test_id)] = 0

        logger.info(
            "quarantine.test_manually_quarantined",
            tenant_id=tenant_id,
            test_id=test_id,
            reason=reason,
            desflake_ticket=desflake_ticket,
        )
        return record

    def is_quarantined(self, tenant_id: str, test_id: str) -> bool:
        """Determina si un test está en cuarentena (activa o probatoria)."""
        tenant_records = self._get_tenant_records(tenant_id)
        record = tenant_records.get(test_id)
        if record is None:
            return False
        return record.status in ("active", "probation")

    def filter_blocking_suite(self, tenant_id: str, test_ids: list[str]) -> list[str]:
        """Excluye los tests en cuarentena activa o probatoria, retornando sólo los bloqueantes."""
        return [tid for tid in test_ids if not self.is_quarantined(tenant_id, tid)]

    def evaluate_run_flakiness(
        self,
        total_tests: int,
        flaky_tests: list[str],
        threshold: float = DEFAULT_FLAKINESS_THRESHOLD,
        run_id: str | None = None,
        tenant_id: str | None = None,
    ) -> RunFlakinessSummaryDTO:
        """Evalúa la tasa global de inestabilidad de una corrida contra el umbral permitido."""
        if total_tests < 0:
            msg = f"El total de pruebas no puede ser negativo: {total_tests}"
            raise ValueError(msg)

        flaky_count = len(flaky_tests)
        rate = (flaky_count / total_tests) if total_tests > 0 else 0.0

        active_quarantined: list[str] = []
        if tenant_id:
            active_quarantined = [
                rec.test_identifier
                for rec in self._get_tenant_records(tenant_id).values()
                if rec.status in ("active", "probation")
            ]

        exceeded = rate > threshold

        return RunFlakinessSummaryDTO(
            run_id=run_id or f"run-{uuid.uuid4().hex[:8]}",
            total_tests=total_tests,
            flaky_tests_count=flaky_count,
            flakiness_rate=round(rate, 4),
            threshold_max=threshold,
            exceeded_threshold=exceeded,
            quarantined_tests_active=sorted(active_quarantined),
        )

    def assert_acceptable_flakiness(self, summary: RunFlakinessSummaryDTO) -> None:
        """Bloquea la compuerta QA si la tasa global de inestabilidad supera el umbral."""
        if summary.exceeded_threshold or summary.flakiness_rate > summary.threshold_max:
            msg = (
                f"Tasa global de inestabilidad ({summary.flakiness_rate * 100:.2f}%) supera el umbral "
                f"máximo permitido ({summary.threshold_max * 100:.2f}%): {summary.flaky_tests_count} "
                f"de {summary.total_tests} tests inestables detectados en corrida '{summary.run_id}'."
            )
            logger.error("quarantine.flakiness_threshold_exceeded", message=msg)
            raise FlakinessThresholdExceededError(msg)

        logger.info(
            "quarantine.flakiness_acceptable",
            rate=summary.flakiness_rate,
            threshold=summary.threshold_max,
        )

    def export_quarantine_report(self, tenant_id: str, output_path: str | Path) -> Path:
        """Exporta el estado de cuarentena del tenant a un archivo JSON determinista para WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        records = [rec.model_dump() for rec in self._get_tenant_records(tenant_id).values()]
        payload = {
            "tenant_id": tenant_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "total_records": len(records),
            "active_quarantined": [
                r["test_identifier"] for r in records if r["status"] in ("active", "probation")
            ],
            "records": records,
        }

        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target_path

    def load_quarantine_report(self, report_path: str | Path) -> list[QuarantineRecordDTO]:
        """Carga un reporte exportado previamente y restaura los registros en memoria."""
        source_path = Path(report_path).resolve()
        if not source_path.exists():
            msg = f"El archivo de reporte no existe: {source_path}"
            raise FileNotFoundError(msg)

        content = source_path.read_text(encoding="utf-8")
        data = json.loads(content)
        tenant_id = str(data.get("tenant_id", "default"))
        records_raw = data.get("records", [])

        tenant_records = self._get_tenant_records(tenant_id)
        loaded_dtos: list[QuarantineRecordDTO] = []
        for r in records_raw:
            dto = QuarantineRecordDTO.model_validate(r)
            tenant_records[dto.test_identifier] = dto
            loaded_dtos.append(dto)
        return loaded_dtos

    # Métodos de compatibilidad defensiva para retry_handler.py
    def record_flaky(
        self,
        case_id: str,
        tenant_id: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        """Notificación defensiva desde RetryHandler."""
        target_tenant = tenant_id or "default"
        self.register_execution(
            tenant_id=target_tenant,
            test_id=case_id,
            criterio_id="CRIT-RETRY",
            result="flaky_pass",
            error_excerpt=error_summary,
        )

    def add_quarantine(
        self,
        case_id: str,
        reason: str,
        tenant_id: str | None = None,
        desflake_ticket: str | None = None,
    ) -> None:
        """Notificación defensiva para agregar a cuarentena desde RetryHandler."""
        target_tenant = tenant_id or "default"
        ticket = desflake_ticket or f"DESFLAKE-{case_id.replace('::', '-')}"
        self.quarantine_test(
            tenant_id=target_tenant,
            test_id=case_id,
            reason=reason,
            desflake_ticket=ticket,
        )
