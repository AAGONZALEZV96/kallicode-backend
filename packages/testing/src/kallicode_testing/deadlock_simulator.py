"""Simulador de Deadlocks (SQLSTATE 40P01) y Transacciones Concurrentes (KC-QA-B6-04).

Ticket: KC-QA-B6-04
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Propósito: Provocar deadlocks cruzados reales sobre PostgreSQL 16 local bajo RLS,
verificar recuperación mediante retries exponenciales con jitter, evitar dobles
aprobaciones de gates bajo concurrencia y exportar reportes WORM inmutables.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

P = ParamSpec("P")
T = TypeVar("T")

_DEADLOCK_SQLSTATE = "40P01"

# ContextVar por corrutina para capturar intentos y deadlocks cuando se usa el decorador
_tx_tracker_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "kallicode_deadlock_tx_tracker",
    default=None,
)


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class UnhandledDeadlockError(RuntimeError):
    """Lanzada cuando un deadlock (40P01) persiste tras agotar los reintentos configurados."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int = 409,
        retry_after_s: int = 1,
        codigo: str = "CONFLICTO_CONCURRENCIA",
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after_s = retry_after_s
        self.codigo = codigo


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-04 (Inmutables)
# ---------------------------------------------------------------------------
class TransactionExecutionDTO(BaseModel):
    """Resultado inmutable de la ejecución concurrente de una transacción SQL."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    tx_id: str
    attempts_count: int
    deadlock_detected: bool
    succeeded: bool
    duration_ms: float
    error_message: str | None = None


class DeadlockReportDTO(BaseModel):
    """Reporte consolidado e inmutable de pruebas de deadlocks y concurrencia SQL."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"deadlock-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_scenarios: int
    deadlocks_provoked: int
    retries_succeeded: int
    unhandled_failures: int
    is_conforming: bool


# ---------------------------------------------------------------------------
# Simulador y Estrategias de Reintento
# ---------------------------------------------------------------------------
class DeadlockSimulator:
    """Orquesta conflictos SQL concurrentes (40P01), backoff exponencial y reportes WORM."""

    @staticmethod
    def is_deadlock_error(exc: BaseException) -> bool:
        """Detecta SQLSTATE 40P01 / DeadlockDetectedError en toda la cadena de causas."""
        seen: set[int] = set()
        queue: list[BaseException] = [exc]

        while queue and len(seen) < 16:
            current = queue.pop(0)
            obj_id = id(current)
            if obj_id in seen:
                continue
            seen.add(obj_id)

            if isinstance(current, asyncpg.DeadlockDetectedError):
                return True

            sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
            if sqlstate == _DEADLOCK_SQLSTATE:
                return True

            msg = str(current).lower()
            if "deadlock detected" in msg or "40p01" in msg:
                return True

            for attr in ("__cause__", "__context__", "orig"):
                nxt = getattr(current, attr, None)
                if isinstance(nxt, BaseException):
                    queue.append(nxt)

        return False

    @staticmethod
    def retry_on_deadlock(
        max_retries: int = 3,
        initial_delay_ms: float = 50.0,
    ) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
        """Decorador asíncrono con backoff exponencial y jitter ante SQLSTATE 40P01."""
        if max_retries < 0:
            msg = "max_retries debe ser mayor o igual a 0"
            raise ValueError(msg)

        def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
            @functools.wraps(func)
            async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                tracker = _tx_tracker_var.get()
                deadlock_seen = False

                for attempt in range(1, max_retries + 2):
                    if tracker is not None:
                        tracker["attempts_count"] = attempt
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        if not DeadlockSimulator.is_deadlock_error(exc):
                            raise

                        deadlock_seen = True
                        if tracker is not None:
                            tracker["deadlock_detected"] = True

                        if attempt > max_retries:
                            logger.error(
                                "deadlock.retries_exhausted",
                                func=func.__name__,
                                attempts=attempt,
                                error=str(exc),
                            )
                            msg = (
                                f"Deadlock persistente (40P01) tras {attempt} intentos en "
                                f"'{func.__name__}': {exc}"
                            )
                            raise UnhandledDeadlockError(
                                msg,
                                http_status=409,
                                retry_after_s=1,
                                codigo="CONFLICTO_CONCURRENCIA",
                            ) from exc

                        base_delay_s = (initial_delay_ms / 1000.0) * (2 ** (attempt - 1))
                        jitter_s = random.uniform(0.0, (initial_delay_ms / 1000.0) * 0.5)
                        delay_s = base_delay_s + jitter_s
                        logger.warning(
                            "deadlock.retry_scheduled",
                            func=func.__name__,
                            attempt=attempt,
                            delay_ms=round(delay_s * 1000.0, 2),
                            deadlock_seen=deadlock_seen,
                        )
                        await asyncio.sleep(delay_s)

                # Inalcanzable por construcción del bucle, resguardo estricto de tipado
                raise UnhandledDeadlockError("Fallo inesperado en bucle de reintentos de deadlock.")

            return wrapper

        return decorator

    @staticmethod
    async def _run_tracked_tx(
        tx_id: str,
        session_factory: Any,
        tx_fn: Callable[[Any, Any], Awaitable[Any]],
        barrier: asyncio.Barrier | asyncio.Event,
    ) -> TransactionExecutionDTO:
        tracker: dict[str, Any] = {"attempts_count": 1, "deadlock_detected": False}
        token = _tx_tracker_var.set(tracker)
        start = time.perf_counter()

        try:
            result = await tx_fn(session_factory, barrier)
            elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)

            if isinstance(result, TransactionExecutionDTO):
                return result

            return TransactionExecutionDTO(
                tx_id=tx_id,
                attempts_count=int(tracker["attempts_count"]),
                deadlock_detected=bool(tracker["deadlock_detected"]),
                succeeded=True,
                duration_ms=elapsed_ms,
                error_message=None,
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)
            is_deadlock = DeadlockSimulator.is_deadlock_error(exc) or bool(
                tracker["deadlock_detected"]
            )
            return TransactionExecutionDTO(
                tx_id=tx_id,
                attempts_count=int(tracker["attempts_count"]),
                deadlock_detected=is_deadlock,
                succeeded=False,
                duration_ms=elapsed_ms,
                error_message=str(exc),
            )
        finally:
            _tx_tracker_var.reset(token)

    @staticmethod
    async def execute_concurrent_updates(
        session_factory: Any,
        tx_a_fn: Callable[[Any, Any], Awaitable[Any]],
        tx_b_fn: Callable[[Any, Any], Awaitable[Any]],
        barrier: asyncio.Barrier | asyncio.Event,
    ) -> tuple[TransactionExecutionDTO, TransactionExecutionDTO]:
        """Ejecuta dos transacciones concurrentes sincronizadas con una barrera."""
        dto_a, dto_b = await asyncio.gather(
            DeadlockSimulator._run_tracked_tx("tx_a", session_factory, tx_a_fn, barrier),
            DeadlockSimulator._run_tracked_tx("tx_b", session_factory, tx_b_fn, barrier),
        )
        return (dto_a, dto_b)

    @staticmethod
    def build_report(
        executions: list[TransactionExecutionDTO],
        total_scenarios: int | None = None,
    ) -> DeadlockReportDTO:
        """Consolida resultados de concurrencia prohibiendo falsos verdes vacíos."""
        if not executions:
            return DeadlockReportDTO(
                total_scenarios=total_scenarios or 0,
                deadlocks_provoked=0,
                retries_succeeded=0,
                unhandled_failures=0,
                is_conforming=False,
            )

        deadlocks_provoked = sum(1 for item in executions if item.deadlock_detected)
        retries_succeeded = sum(
            1 for item in executions if item.deadlock_detected and item.succeeded
        )
        unhandled_failures = sum(1 for item in executions if not item.succeeded)
        scenarios_count = total_scenarios if total_scenarios is not None else len(executions)

        return DeadlockReportDTO(
            total_scenarios=scenarios_count,
            deadlocks_provoked=deadlocks_provoked,
            retries_succeeded=retries_succeeded,
            unhandled_failures=unhandled_failures,
            is_conforming=unhandled_failures == 0,
        )

    @staticmethod
    def export_report_json(report: DeadlockReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte de deadlocks a JSON determinista listo para auditoría WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "deadlock.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path
