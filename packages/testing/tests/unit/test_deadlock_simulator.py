"""Pruebas unitarias para DeadlockSimulator y DTOs de concurrencia (KC-QA-B6-04).

Ticket: KC-QA-B6-04
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from kallicode_testing.deadlock_simulator import (
    DeadlockReportDTO,
    DeadlockSimulator,
    TransactionExecutionDTO,
    UnhandledDeadlockError,
)
from pydantic import ValidationError


def test_inmutabilidad_dtos_deadlock() -> None:
    """Confirma que TransactionExecutionDTO y DeadlockReportDTO tienen frozen=True."""
    tx_dto = TransactionExecutionDTO(
        tx_id="tx_a",
        attempts_count=2,
        deadlock_detected=True,
        succeeded=True,
        duration_ms=42.5,
    )
    report_dto = DeadlockReportDTO(
        total_scenarios=1,
        deadlocks_provoked=1,
        retries_succeeded=1,
        unhandled_failures=0,
        is_conforming=True,
    )

    with pytest.raises(ValidationError):
        tx_dto.succeeded = False

    with pytest.raises(ValidationError):
        report_dto.is_conforming = False


def test_deteccion_sqlstate_40p01_y_cadena_causas() -> None:
    """Detecta DeadlockDetectedError (40P01) directo, por atributo sqlstate y por cadena."""
    direct = asyncpg.DeadlockDetectedError("deadlock detected")
    assert DeadlockSimulator.is_deadlock_error(direct) is True

    class _FakeDbapiWrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            super().__init__("wrapped operational error")
            self.orig = orig

    class _PgCodeErr(Exception):
        sqlstate = "40P01"

    assert DeadlockSimulator.is_deadlock_error(_FakeDbapiWrapper(_PgCodeErr())) is True
    assert DeadlockSimulator.is_deadlock_error(RuntimeError("SQLSTATE 40P01 encountered")) is True
    assert DeadlockSimulator.is_deadlock_error(ValueError("validation error")) is False


@pytest.mark.asyncio
async def test_retry_on_deadlock_recuperacion_backoff_y_agotamiento() -> None:
    """Valida backoff exponencial exitoso, paso directo de otros errores y UnhandledDeadlockError."""
    calls = 0

    @DeadlockSimulator.retry_on_deadlock(max_retries=3, initial_delay_ms=10.0)
    async def flaky_tx() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise asyncpg.DeadlockDetectedError("deadlock detected")
        return "committed"

    with patch("kallicode_testing.deadlock_simulator.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        result = await flaky_tx()
        assert result == "committed"
        assert calls == 3
        assert mock_sleep.await_count == 2
        # El segundo delay base (20ms) debe ser mayor o igual al rango del primero (10-15ms)
        first_delay = mock_sleep.await_args_list[0].args[0]
        second_delay = mock_sleep.await_args_list[1].args[0]
        assert first_delay >= 0.01
        assert second_delay >= 0.02

    # Agotamiento de reintentos lanza UnhandledDeadlockError con metadata HTTP controlada
    persistent_calls = 0

    @DeadlockSimulator.retry_on_deadlock(max_retries=2, initial_delay_ms=5.0)
    async def always_deadlocked() -> None:
        nonlocal persistent_calls
        persistent_calls += 1
        raise asyncpg.DeadlockDetectedError("persistent deadlock")

    with (
        patch("kallicode_testing.deadlock_simulator.asyncio.sleep", new=AsyncMock()),
        pytest.raises(UnhandledDeadlockError) as exc_info,
    ):
        await always_deadlocked()

    assert persistent_calls == 3  # 1 intento inicial + 2 reintentos
    assert exc_info.value.http_status in (409, 503)
    assert exc_info.value.retry_after_s >= 1

    # Errores distintos a 40P01 no se reintentan
    non_deadlock_calls = 0

    @DeadlockSimulator.retry_on_deadlock(max_retries=3, initial_delay_ms=5.0)
    async def logic_error() -> None:
        nonlocal non_deadlock_calls
        non_deadlock_calls += 1
        raise ValueError("campo invalido")

    with pytest.raises(ValueError, match="campo invalido"):
        await logic_error()
    assert non_deadlock_calls == 1

    with pytest.raises(ValueError, match="max_retries"):
        DeadlockSimulator.retry_on_deadlock(max_retries=-1)


@pytest.mark.asyncio
async def test_execute_concurrent_updates_tracking_y_fallos() -> None:
    """Verifica execute_concurrent_updates rastreando retries, éxitos y fallos controlados."""
    barrier = asyncio.Barrier(2)
    tx_a_calls = 0

    @DeadlockSimulator.retry_on_deadlock(max_retries=2, initial_delay_ms=5.0)
    async def tx_a(_sf: object, bar: asyncio.Barrier) -> str:
        nonlocal tx_a_calls
        tx_a_calls += 1
        if tx_a_calls == 1:
            await bar.wait()
            raise asyncpg.DeadlockDetectedError("deadlock victim")
        return "ok"

    @DeadlockSimulator.retry_on_deadlock(max_retries=2, initial_delay_ms=5.0)
    async def tx_b(_sf: object, bar: asyncio.Barrier) -> str:
        await bar.wait()
        return "ok"

    dto_a, dto_b = await DeadlockSimulator.execute_concurrent_updates(
        None,
        tx_a,
        tx_b,
        barrier,
    )

    assert dto_a.tx_id == "tx_a"
    assert dto_a.attempts_count == 2
    assert dto_a.deadlock_detected is True
    assert dto_a.succeeded is True

    assert dto_b.tx_id == "tx_b"
    assert dto_b.attempts_count == 1
    assert dto_b.deadlock_detected is False
    assert dto_b.succeeded is True

    # Simular fallo no recuperado en una transacción
    async def failing_tx(_sf: object, _bar: asyncio.Event) -> None:
        raise UnhandledDeadlockError("agotado")

    async def ok_tx(_sf: object, _bar: asyncio.Event) -> TransactionExecutionDTO:
        return TransactionExecutionDTO(
            tx_id="tx_b",
            attempts_count=1,
            deadlock_detected=False,
            succeeded=True,
            duration_ms=1.2,
        )

    f_a, f_b = await DeadlockSimulator.execute_concurrent_updates(
        None,
        failing_tx,
        ok_tx,
        asyncio.Event(),
    )
    assert f_a.succeeded is False
    assert f_a.error_message == "agotado"
    assert f_b.succeeded is True


def test_build_report_y_export_json(tmp_path: Path) -> None:
    """Valida prevención de falsos verdes vacíos y exportación WORM determinista."""
    empty = DeadlockSimulator.build_report([])
    assert empty.is_conforming is False
    assert empty.total_scenarios == 0

    executions = [
        TransactionExecutionDTO(
            tx_id="tx_a",
            attempts_count=2,
            deadlock_detected=True,
            succeeded=True,
            duration_ms=35.0,
        ),
        TransactionExecutionDTO(
            tx_id="tx_b",
            attempts_count=1,
            deadlock_detected=False,
            succeeded=True,
            duration_ms=22.0,
        ),
    ]
    report = DeadlockSimulator.build_report(executions, total_scenarios=1)
    assert report.is_conforming is True
    assert report.deadlocks_provoked == 1
    assert report.retries_succeeded == 1
    assert report.unhandled_failures == 0

    exported = DeadlockSimulator.export_report_json(
        report,
        tmp_path / "worm" / "deadlock-test-report.json",
    )
    assert exported.exists()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["report_id"] == report.report_id
    assert data["deadlocks_provoked"] == 1
    assert data["is_conforming"] is True
