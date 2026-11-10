"""
Ticket: KC-QA-B4-10
Suite de Re-ejecución Automática de Tests Fallidos (Flaky Retry) (retry_handler.py).
Orquesta la re-ejecución aislada y determinista de funciones o comandos de prueba fallidos.
Aplica la regla de negocio estricta: todo test que falle inicialmente pero apruebe en el
reintento debe ser clasificado obligatoriamente como 'flaky', NUNCA como 'passed', registrando
el historial detallado de intentos, tiempos y errores, y notificando a quarantine_store.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

# Estados posibles del veredicto consolidado
FlakyFinalStatus = Literal["passed", "flaky", "failed"]


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-10
# ---------------------------------------------------------------------------
class AttemptDetailDTO(BaseModel):
    """
    Registra el resultado detallado e inmutable de un intento individual de ejecución.
    """

    model_config = ConfigDict(frozen=True)

    attempt_number: int = Field(ge=1, description="Numeración basada en 1")
    success: bool
    duration_ms: float = Field(ge=0.0)
    error_summary: str | None = None
    timestamp: str  # ISO 8601 UTC


class FlakyExecutionResultDTO(BaseModel):
    """
    Veredicto consolidado e inmutable tras la ejecución y posibles reintentos de una prueba.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    final_status: FlakyFinalStatus
    attempts_count: int = Field(ge=1)
    attempts_history: list[AttemptDetailDTO]
    total_duration_ms: float = Field(ge=0.0)


class RetryConfigDTO(BaseModel):
    """
    Configuración inmutable de la política de reintento.
    """

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(
        default=1, ge=0, description="Cantidad máxima de reintentos adicionales"
    )
    delay_between_retries_sec: float = Field(
        default=0.5, ge=0.0, description="Pausa entre reintentos en segundos"
    )


# ---------------------------------------------------------------------------
# Clase Principal FlakyRetryHandler
# ---------------------------------------------------------------------------
class FlakyRetryHandler:
    """
    Gestor de re-ejecución de pruebas con detección de intermitencias (flaky tests).
    Garantiza que ningún caso recuperado por reintento sea marcado como 'passed'.
    """

    def __init__(
        self,
        config: RetryConfigDTO | None = None,
        quarantine_store: Any | None = None,
    ) -> None:
        self.config = config or RetryConfigDTO()
        self.quarantine_store = quarantine_store

    def execute_with_retry(
        self,
        case_id: str,
        executable_fn: Callable[[], tuple[bool, str | None] | bool],
        tenant_id: str | None = None,
    ) -> FlakyExecutionResultDTO:
        """
        Ejecuta síncronamente una prueba con reintento determinista ante fallos.
        - Si aprueba en el intento 1: final_status = 'passed'.
        - Si falla en el intento 1 pero aprueba en reintento: final_status = 'flaky' (MANDATORIO).
        - Si fallan todos los intentos: final_status = 'failed'.
        """
        attempts_history: list[AttemptDetailDTO] = []
        max_attempts = 1 + self.config.max_retries
        total_start = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            if attempt > 1 and self.config.delay_between_retries_sec > 0:
                time.sleep(self.config.delay_between_retries_sec)

            attempt_start = time.perf_counter()
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            success = False
            error_summary: str | None = None

            try:
                raw_res = executable_fn()
                if isinstance(raw_res, tuple):
                    success = bool(raw_res[0])
                    error_summary = str(raw_res[1]) if raw_res[1] is not None else None
                else:
                    success = bool(raw_res)
                    error_summary = None if success else "Test returned False"
            except Exception as exc:
                success = False
                error_summary = f"{type(exc).__name__}: {exc}"

            duration_ms = (time.perf_counter() - attempt_start) * 1000.0

            detail = AttemptDetailDTO(
                attempt_number=attempt,
                success=success,
                duration_ms=round(duration_ms, 2),
                error_summary=error_summary,
                timestamp=now_iso,
            )
            attempts_history.append(detail)

            # Caso 1: Éxito en el primer intento -> PASSED
            if attempt == 1 and success:
                total_duration = (time.perf_counter() - total_start) * 1000.0
                return FlakyExecutionResultDTO(
                    case_id=case_id,
                    final_status="passed",
                    attempts_count=1,
                    attempts_history=attempts_history,
                    total_duration_ms=round(total_duration, 2),
                )

            # Caso 2: Éxito en reintento posterior -> FLAKY (NUNCA PASSED)
            if attempt > 1 and success:
                total_duration = (time.perf_counter() - total_start) * 1000.0
                logger.warning(
                    "retry_handler.flaky_test_detected",
                    case_id=case_id,
                    attempt_recovered=attempt,
                    tenant_id=tenant_id,
                )
                self._notify_quarantine(case_id, attempts_history[0].error_summary, tenant_id)
                return FlakyExecutionResultDTO(
                    case_id=case_id,
                    final_status="flaky",
                    attempts_count=attempt,
                    attempts_history=attempts_history,
                    total_duration_ms=round(total_duration, 2),
                )

        # Caso 3: Fallo permanente en todos los intentos -> FAILED
        total_duration = (time.perf_counter() - total_start) * 1000.0
        logger.error(
            "retry_handler.test_persistently_failed",
            case_id=case_id,
            attempts_count=len(attempts_history),
        )
        return FlakyExecutionResultDTO(
            case_id=case_id,
            final_status="failed",
            attempts_count=len(attempts_history),
            attempts_history=attempts_history,
            total_duration_ms=round(total_duration, 2),
        )

    async def execute_with_retry_async(
        self,
        case_id: str,
        executable_fn: Callable[
            [],
            Awaitable[tuple[bool, str | None] | bool] | tuple[bool, str | None] | bool,
        ],
        tenant_id: str | None = None,
    ) -> FlakyExecutionResultDTO:
        """
        Variante asíncrona nativa para corrutinas y suites asíncronas de Playwright/Pytest.
        """
        attempts_history: list[AttemptDetailDTO] = []
        max_attempts = 1 + self.config.max_retries
        total_start = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            if attempt > 1 and self.config.delay_between_retries_sec > 0:
                await asyncio.sleep(self.config.delay_between_retries_sec)

            attempt_start = time.perf_counter()
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            success = False
            error_summary: str | None = None

            try:
                res = executable_fn()
                if inspect.isawaitable(res):
                    raw_res = await res
                else:
                    raw_res = res

                if isinstance(raw_res, tuple):
                    success = bool(raw_res[0])
                    error_summary = str(raw_res[1]) if raw_res[1] is not None else None
                else:
                    success = bool(raw_res)
                    error_summary = None if success else "Test returned False"
            except Exception as exc:
                success = False
                error_summary = f"{type(exc).__name__}: {exc}"

            duration_ms = (time.perf_counter() - attempt_start) * 1000.0

            detail = AttemptDetailDTO(
                attempt_number=attempt,
                success=success,
                duration_ms=round(duration_ms, 2),
                error_summary=error_summary,
                timestamp=now_iso,
            )
            attempts_history.append(detail)

            # Caso 1: Éxito en el primer intento -> PASSED
            if attempt == 1 and success:
                total_duration = (time.perf_counter() - total_start) * 1000.0
                return FlakyExecutionResultDTO(
                    case_id=case_id,
                    final_status="passed",
                    attempts_count=1,
                    attempts_history=attempts_history,
                    total_duration_ms=round(total_duration, 2),
                )

            # Caso 2: Éxito en reintento posterior -> FLAKY (NUNCA PASSED)
            if attempt > 1 and success:
                total_duration = (time.perf_counter() - total_start) * 1000.0
                logger.warning(
                    "retry_handler.flaky_test_detected_async",
                    case_id=case_id,
                    attempt_recovered=attempt,
                    tenant_id=tenant_id,
                )
                self._notify_quarantine(case_id, attempts_history[0].error_summary, tenant_id)
                return FlakyExecutionResultDTO(
                    case_id=case_id,
                    final_status="flaky",
                    attempts_count=attempt,
                    attempts_history=attempts_history,
                    total_duration_ms=round(total_duration, 2),
                )

        # Caso 3: Fallo permanente en todos los intentos -> FAILED
        total_duration = (time.perf_counter() - total_start) * 1000.0
        logger.error(
            "retry_handler.test_persistently_failed_async",
            case_id=case_id,
            attempts_count=len(attempts_history),
        )
        return FlakyExecutionResultDTO(
            case_id=case_id,
            final_status="failed",
            attempts_count=len(attempts_history),
            attempts_history=attempts_history,
            total_duration_ms=round(total_duration, 2),
        )

    def _notify_quarantine(
        self,
        case_id: str,
        initial_error: str | None,
        tenant_id: str | None,
    ) -> None:
        """Notifica defensivamente al almacén de cuarentena si está configurado."""
        if self.quarantine_store is None:
            return

        try:
            if hasattr(self.quarantine_store, "record_flaky") and callable(
                self.quarantine_store.record_flaky
            ):
                self.quarantine_store.record_flaky(
                    case_id=case_id,
                    tenant_id=tenant_id,
                    error_summary=initial_error,
                )
            elif hasattr(self.quarantine_store, "add_quarantine") and callable(
                self.quarantine_store.add_quarantine
            ):
                self.quarantine_store.add_quarantine(
                    case_id=case_id,
                    reason=initial_error or "Falla intermitente recuperada en reintento",
                    tenant_id=tenant_id,
                )
            elif callable(self.quarantine_store):
                self.quarantine_store(case_id, initial_error, tenant_id)
        except Exception as exc:
            logger.warning("retry_handler.quarantine_store_error", error=str(exc))
