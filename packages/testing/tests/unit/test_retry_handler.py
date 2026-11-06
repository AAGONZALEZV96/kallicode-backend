"""
Ticket: KC-QA-B4-10
Suite de pruebas unitarias y de resiliencia para FlakyRetryHandler (retry_handler.py).
Valida:
1. test_ejecucion_exitosa_primer_intento_retorna_passed: Éxito en primer intento retorna 'passed' con 1 intento.
2. test_deteccion_flaky_en_reintento_retorna_flaky_y_no_passed: Falla inicial y recuperación en reintento es 'flaky', NUNCA 'passed'.
3. test_fallo_persistente_en_ambos_intentos_retorna_failed: Fallo permanente retorna 'failed' con historial íntegro.
4. test_resiliencia_y_retardo_configurable_en_reintentos: Pausas configurables e inmutabilidad frozen.
5. test_manejo_excepciones_inesperadas_en_executable_fn: Excepciones capturadas como fallo sin abortar el runner.
6. test_ejecucion_asincrona_con_reintento: Soporte para corrutinas asíncronas con execute_with_retry_async.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from kallicode_testing.retry_handler import (
    AttemptDetailDTO,
    FlakyExecutionResultDTO,
    FlakyRetryHandler,
    RetryConfigDTO,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: test_ejecucion_exitosa_primer_intento_retorna_passed
# ---------------------------------------------------------------------------
def test_ejecucion_exitosa_primer_intento_retorna_passed() -> None:
    """
    Prueba 1: Simular función ejecutable que aprueba en el primer intento.
    Validaciones:
    - final_status == 'passed'.
    - attempts_count == 1.
    - Historial contiene exactamente 1 detalle con success=True.
    - total_duration_ms >= 0.
    """
    handler = FlakyRetryHandler()
    mock_fn = MagicMock(return_value=(True, None))

    result = handler.execute_with_retry(case_id="AC-01:test_login", executable_fn=mock_fn)

    assert result.final_status == "passed"
    assert result.attempts_count == 1
    assert len(result.attempts_history) == 1
    assert result.attempts_history[0].attempt_number == 1
    assert result.attempts_history[0].success is True
    assert result.attempts_history[0].error_summary is None
    assert result.total_duration_ms >= 0.0
    mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# Prueba 2: test_deteccion_flaky_en_reintento_retorna_flaky_y_no_passed
# ---------------------------------------------------------------------------
def test_deteccion_flaky_en_reintento_retorna_flaky_y_no_passed() -> None:
    """
    Prueba 2: Simular intento 1 fallido e intento 2 exitoso.
    Validaciones:
    - Regla mandatoria: final_status es obligatoriamente 'flaky'.
    - final_status NUNCA es 'passed'.
    - attempts_count == 2.
    - Notifica a quarantine_store.
    """
    mock_quarantine = MagicMock()
    config = RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01)
    handler = FlakyRetryHandler(config=config, quarantine_store=mock_quarantine)

    # Intento 1: Falla, Intento 2: Aprueba
    side_effects = [
        (False, "ElementNotVisible: #submit_btn no listo en DOM"),
        (True, None),
    ]
    call_index = 0

    def intermittent_test() -> tuple[bool, str | None]:
        nonlocal call_index
        res = side_effects[call_index]
        call_index += 1
        return res

    result = handler.execute_with_retry(
        case_id="AC-02:test_checkout",
        executable_fn=intermittent_test,
        tenant_id="tenant-alpha",
    )

    assert result.final_status == "flaky"
    assert str(result.final_status) != "passed"
    assert result.attempts_count == 2
    assert len(result.attempts_history) == 2

    # Intento 1 registrado como fallo con su error
    assert result.attempts_history[0].success is False
    assert "ElementNotVisible" in (result.attempts_history[0].error_summary or "")

    # Intento 2 registrado como éxito
    assert result.attempts_history[1].success is True

    # Validar reporte a cuarentena
    mock_quarantine.record_flaky.assert_called_once_with(
        case_id="AC-02:test_checkout",
        tenant_id="tenant-alpha",
        error_summary="ElementNotVisible: #submit_btn no listo en DOM",
    )


# ---------------------------------------------------------------------------
# Prueba 3: test_fallo_persistente_en_ambos_intentos_retorna_failed
# ---------------------------------------------------------------------------
def test_fallo_persistente_en_ambos_intentos_retorna_failed() -> None:
    """
    Prueba 3: Simular fallo permanente en todos los intentos disponibles.
    Validaciones:
    - final_status == 'failed'.
    - attempts_count == 2 (1 intento original + 1 reintento).
    - attempts_history conserva ambas trazas de error.
    """
    config = RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01)
    handler = FlakyRetryHandler(config=config)

    persistent_fail = MagicMock(return_value=(False, "HTTP 500: Database Deadlock"))

    result = handler.execute_with_retry(
        case_id="AC-03:test_deadlock",
        executable_fn=persistent_fail,
    )

    assert result.final_status == "failed"
    assert result.attempts_count == 2
    assert len(result.attempts_history) == 2
    assert result.attempts_history[0].success is False
    assert result.attempts_history[1].success is False
    assert result.attempts_history[0].error_summary == "HTTP 500: Database Deadlock"
    assert result.attempts_history[1].error_summary == "HTTP 500: Database Deadlock"
    assert persistent_fail.call_count == 2


# ---------------------------------------------------------------------------
# Prueba 4: test_resiliencia_y_retardo_configurable_en_reintentos
# ---------------------------------------------------------------------------
def test_resiliencia_y_retardo_configurable_en_reintentos() -> None:
    """
    Prueba 4: Verificar retardo configurable entre reintentos e inmutabilidad Pydantic (frozen=True).
    """
    # 1. Respeto de pausa configurable
    delay = 0.08
    config = RetryConfigDTO(max_retries=1, delay_between_retries_sec=delay)
    handler = FlakyRetryHandler(config=config)

    start_time = time.perf_counter()
    handler.execute_with_retry("AC-04:test_timing", lambda: (False, "Falla forzada"))
    elapsed = time.perf_counter() - start_time

    assert elapsed >= delay

    # 2. Inmutabilidad DTOs
    detail = AttemptDetailDTO(
        attempt_number=1,
        success=True,
        duration_ms=10.0,
        timestamp="2026-11-05T22:00:00Z",
    )
    with pytest.raises(ValidationError):
        detail.success = False

    result_dto = FlakyExecutionResultDTO(
        case_id="AC-05",
        final_status="passed",
        attempts_count=1,
        attempts_history=[detail],
        total_duration_ms=10.0,
    )
    with pytest.raises(ValidationError):
        result_dto.final_status = "flaky"


# ---------------------------------------------------------------------------
# Prueba 5: test_manejo_excepciones_inesperadas_en_executable_fn
# ---------------------------------------------------------------------------
def test_manejo_excepciones_inesperadas_en_executable_fn() -> None:
    """
    Prueba 5: Cuando executable_fn lanza una excepción inesperada (AssertionError, RuntimeError),
    el handler la captura como fallo estructurado sin crashear.
    """
    handler = FlakyRetryHandler(
        config=RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01)
    )

    def crashing_test() -> tuple[bool, str | None]:
        raise AssertionError("assert 200 == 500: backend unexpected response")

    result = handler.execute_with_retry("AC-06:test_crash", crashing_test)

    assert result.final_status == "failed"
    assert result.attempts_count == 2
    assert "AssertionError" in (result.attempts_history[0].error_summary or "")


# ---------------------------------------------------------------------------
# Prueba 6: test_ejecucion_asincrona_con_reintento
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ejecucion_asincrona_con_reintento() -> None:
    """
    Prueba 6: Variante asíncrona execute_with_retry_async operando sobre corrutinas.
    """
    mock_quarantine = MagicMock()
    config = RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01)
    handler = FlakyRetryHandler(config=config, quarantine_store=mock_quarantine)

    attempts = 0

    async def async_coroutine_test() -> tuple[bool, str | None]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return (False, "TimeoutError: Playwright selector wait timed out")
        return (True, None)

    result = await handler.execute_with_retry_async(
        case_id="AC-07:async_e2e",
        executable_fn=async_coroutine_test,
        tenant_id="tenant-async",
    )

    assert result.final_status == "flaky"
    assert str(result.final_status) != "passed"
    assert result.attempts_count == 2
    assert mock_quarantine.record_flaky.called


# ---------------------------------------------------------------------------
# Prueba 7: test_cobertura_retornos_booleanos_y_variantes_async_y_quarantine
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cobertura_retornos_booleanos_y_variantes_async_y_quarantine() -> None:
    """
    Prueba 7: Cobertura exhaustiva de:
    - Retornos de tipo booleano puro (bool) en sync y async.
    - Éxito al primer intento y fallo persistente en async.
    - quarantine_store con método add_quarantine, función callable y manejo de excepciones.
    """
    # 1. Retorno booleano puro sync
    handler = FlakyRetryHandler(
        config=RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01)
    )
    res_bool_pass = handler.execute_with_retry("AC-08", lambda: True)
    assert res_bool_pass.final_status == "passed"

    res_bool_fail = handler.execute_with_retry("AC-09", lambda: False)
    assert res_bool_fail.final_status == "failed"

    # 2. Async primer intento exitoso
    async def pass_async() -> bool:
        return True

    res_async_pass = await handler.execute_with_retry_async("AC-10", pass_async)
    assert res_async_pass.final_status == "passed"

    # 3. Async fallo permanente
    async def fail_async() -> tuple[bool, str]:
        return (False, "Persistent timeout")

    res_async_fail = await handler.execute_with_retry_async("AC-11", fail_async)
    assert res_async_fail.final_status == "failed"
    assert res_async_fail.attempts_count == 2

    # 4. quarantine_store con add_quarantine
    mock_store_add = MagicMock()
    del mock_store_add.record_flaky
    handler_add = FlakyRetryHandler(
        config=RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01),
        quarantine_store=mock_store_add,
    )
    calls = 0

    def flaky_func() -> tuple[bool, str | None]:
        nonlocal calls
        calls += 1
        return (calls > 1, "Falla intento 1")

    res_flaky_add = handler_add.execute_with_retry("AC-12", flaky_func, tenant_id="t-add")
    assert res_flaky_add.final_status == "flaky"
    mock_store_add.add_quarantine.assert_called_once()

    # 5. quarantine_store como callable y con excepción
    callable_invoked = False

    def mock_callable_fn(case_id: str, error: str | None, tenant: str | None) -> None:
        nonlocal callable_invoked
        callable_invoked = True
        raise RuntimeError("Quarantine DB unreachable")

    handler_exc = FlakyRetryHandler(
        config=RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.01),
        quarantine_store=mock_callable_fn,
    )
    calls2 = 0

    def flaky_func2() -> tuple[bool, str | None]:
        nonlocal calls2
        calls2 += 1
        return (calls2 > 1, "Falla intento 1")

    # No debe propagar excepciones si quarantine_store falla
    res_flaky_exc = handler_exc.execute_with_retry("AC-13", flaky_func2)
    assert res_flaky_exc.final_status == "flaky"
    assert callable_invoked is True
