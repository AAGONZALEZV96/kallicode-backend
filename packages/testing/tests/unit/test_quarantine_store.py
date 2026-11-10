"""Pruebas Unitarias del Gestor de Cuarentena de Tests Inestables (QuarantineStore).

Ticket: KC-QA-B4-04 (Cierra hallazgo QA-F09)
Épica: EQA7 - Motor de Pruebas Autónomo y E2E
Propósito: Validar la detección de rachas inestables, auto-cuarentena, exclusión de la suite
bloqueante, compuerta dura de tasa máxima de inestabilidad (Flakiness Rate Gate), graduación
controlada, exigencia de motivo y ticket desflake, inmutabilidad y aislamiento multi-tenant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.quarantine_store import (
    DEFAULT_FLAKINESS_THRESHOLD,
    FlakinessThresholdExceededError,
    FlakyHistoryEntryDTO,
    QuarantineRecordDTO,
    QuarantineStore,
    QuarantineValidationError,
    RunFlakinessSummaryDTO,
)
from kallicode_testing.retry_handler import FlakyRetryHandler, RetryConfigDTO
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: test_deteccion_racha_inestable_y_auto_cuarentena
# ---------------------------------------------------------------------------
def test_deteccion_racha_inestable_y_auto_cuarentena() -> None:
    """Verifica la detección automática de 3 rachas inestables y transición a 'active'."""
    store = QuarantineStore(observation_window=10)
    tenant_id = "tenant-test-01"
    test_id = "tests/unit/test_auth.py::test_jwt_expiration"
    crit_id = "CRIT-AUTH-01"

    # Intento 1: PASS -> streak 0, status retired
    rec1 = store.register_execution(tenant_id, test_id, crit_id, result="pass")
    assert rec1.status == "retired"
    assert rec1.streak_count == 0
    assert store.is_quarantined(tenant_id, test_id) is False

    # Intento 2: FAIL -> racha 1
    rec2 = store.register_execution(
        tenant_id,
        test_id,
        crit_id,
        result="fail",
        error_excerpt="AssertionError: token expired early",
    )
    assert rec2.status == "retired"
    assert rec2.streak_count == 1
    assert store.is_quarantined(tenant_id, test_id) is False

    # Intento 3: FLAKY_PASS -> racha 2
    rec3 = store.register_execution(
        tenant_id,
        test_id,
        crit_id,
        result="flaky_pass",
        error_excerpt="Timeout on attempt 1",
    )
    assert rec3.status == "retired"
    assert rec3.streak_count == 2
    assert store.is_quarantined(tenant_id, test_id) is False

    # Intento 4: FAIL -> racha 3 => AUTO-CUARENTENA ACTIVADA
    rec4 = store.register_execution(
        tenant_id,
        test_id,
        crit_id,
        result="fail",
        error_excerpt="AssertionError: Connection refused",
    )
    assert rec4.status == "active"
    assert rec4.streak_count == 3
    assert store.is_quarantined(tenant_id, test_id) is True
    assert len(rec4.history) == 4


# ---------------------------------------------------------------------------
# Prueba 2: test_exclusion_de_suite_bloqueante
# ---------------------------------------------------------------------------
def test_exclusion_de_suite_bloqueante() -> None:
    """Valida que los tests en cuarentena (active o probation) no bloqueen el pipeline."""
    store = QuarantineStore()
    tenant_id = "tenant-ci-01"

    all_tests = [
        "tests/unit/test_crypto.py::test_aes_gcm",
        "tests/unit/test_flaky.py::test_socket_race",
        "tests/unit/test_db.py::test_rls_tenant",
        "tests/unit/test_probation.py::test_intermittent_lock",
    ]

    # Cuarentenar test_socket_race manualmente en active
    store.quarantine_test(
        tenant_id=tenant_id,
        test_id="tests/unit/test_flaky.py::test_socket_race",
        reason="Condición de carrera recurrente en sockets",
        desflake_ticket="DESFLAKE-101",
    )

    # test_intermittent_lock se pone en active y recibe 1 pass -> pasa a probation
    store.quarantine_test(
        tenant_id=tenant_id,
        test_id="tests/unit/test_probation.py::test_intermittent_lock",
        reason="Bloqueo intermitente de tabla",
        desflake_ticket="DESFLAKE-102",
    )
    store.register_execution(
        tenant_id=tenant_id,
        test_id="tests/unit/test_probation.py::test_intermittent_lock",
        criterio_id="CRIT-DB-02",
        result="pass",
    )

    # Ambos deben considerarse en cuarentena
    assert store.is_quarantined(tenant_id, "tests/unit/test_flaky.py::test_socket_race") is True
    assert (
        store.is_quarantined(tenant_id, "tests/unit/test_probation.py::test_intermittent_lock")
        is True
    )

    # Filtrar suite bloqueante
    blocking_suite = store.filter_blocking_suite(tenant_id, all_tests)
    assert len(blocking_suite) == 2
    assert "tests/unit/test_crypto.py::test_aes_gcm" in blocking_suite
    assert "tests/unit/test_db.py::test_rls_tenant" in blocking_suite
    assert "tests/unit/test_flaky.py::test_socket_race" not in blocking_suite
    assert "tests/unit/test_probation.py::test_intermittent_lock" not in blocking_suite


# ---------------------------------------------------------------------------
# Prueba 3: test_bloqueo_por_exceso_de_tasa_de_inestabilidad
# ---------------------------------------------------------------------------
def test_bloqueo_por_exceso_de_tasa_de_inestabilidad() -> None:
    """Verifica que si la tasa de inestabilidad supera el 2%, la compuerta dura detiene el deploy."""
    store = QuarantineStore()
    tenant_id = "tenant-gate-01"

    # Caso A: 100 pruebas, 3 flaky -> tasa = 3.0% > 2.0% (Bloqueo)
    flaky_list_fail = ["test_a", "test_b", "test_c"]
    summary_fail = store.evaluate_run_flakiness(
        total_tests=100,
        flaky_tests=flaky_list_fail,
        threshold=DEFAULT_FLAKINESS_THRESHOLD,
        run_id="run-nightly-001",
        tenant_id=tenant_id,
    )

    assert summary_fail.flakiness_rate == 0.03
    assert summary_fail.threshold_max == 0.02
    assert summary_fail.exceeded_threshold is True

    with pytest.raises(FlakinessThresholdExceededError) as exc_info:
        store.assert_acceptable_flakiness(summary_fail)
    assert "Tasa global de inestabilidad (3.00%) supera el umbral" in str(exc_info.value)

    # Caso B: 100 pruebas, 1 flaky -> tasa = 1.0% <= 2.0% (Aprobado)
    flaky_list_pass = ["test_a"]
    summary_pass = store.evaluate_run_flakiness(
        total_tests=100,
        flaky_tests=flaky_list_pass,
        threshold=DEFAULT_FLAKINESS_THRESHOLD,
        run_id="run-nightly-002",
        tenant_id=tenant_id,
    )

    assert summary_pass.flakiness_rate == 0.01
    assert summary_pass.exceeded_threshold is False
    # No debe levantar excepción
    store.assert_acceptable_flakiness(summary_pass)


# ---------------------------------------------------------------------------
# Prueba 4: test_graduacion_y_salida_controlada_de_cuarentena
# ---------------------------------------------------------------------------
def test_graduacion_y_salida_controlada_de_cuarentena() -> None:
    """Valida la disciplina de graduación (2 PASS consecutivos) y regresión ante fallos."""
    store = QuarantineStore()
    tenant_id = "tenant-grad-01"
    test_id = "tests/unit/test_cache.py::test_redis_eviction"
    crit_id = "CRIT-CACHE-01"

    # Colocar inicialmente en cuarentena activa
    store.quarantine_test(
        tenant_id=tenant_id,
        test_id=test_id,
        reason="Inestabilidad por timeout en Redis",
        desflake_ticket="DESFLAKE-200",
    )
    assert store.is_quarantined(tenant_id, test_id) is True

    # 1er PASS -> Pasa a probation (sigue en cuarentena, is_quarantined == True)
    rec_pass1 = store.register_execution(tenant_id, test_id, crit_id, result="pass")
    assert rec_pass1.status == "probation"
    assert store.is_quarantined(tenant_id, test_id) is True

    # Si en probation falla, debe volver a active
    rec_relapse = store.register_execution(
        tenant_id,
        test_id,
        crit_id,
        result="fail",
        error_excerpt="Redis connection timeout",
    )
    assert rec_relapse.status == "active"
    assert store.is_quarantined(tenant_id, test_id) is True

    # Ahora sí: 1er PASS nuevamente -> probation
    rec_pass1_again = store.register_execution(tenant_id, test_id, crit_id, result="pass")
    assert rec_pass1_again.status == "probation"
    assert store.is_quarantined(tenant_id, test_id) is True

    # 2do PASS consecutivo -> GRADUADO (status == 'graduated', streak == 0)
    rec_pass2 = store.register_execution(tenant_id, test_id, crit_id, result="pass")
    assert rec_pass2.status == "graduated"
    assert rec_pass2.streak_count == 0
    assert store.is_quarantined(tenant_id, test_id) is False

    # El test graduado ahora forma parte de la suite bloqueante
    blocking = store.filter_blocking_suite(tenant_id, [test_id, "test_other"])
    assert test_id in blocking
    assert len(blocking) == 2


# ---------------------------------------------------------------------------
# Prueba 5: test_exigencia_de_ticket_desflake_y_motivo
# ---------------------------------------------------------------------------
def test_exigencia_de_ticket_desflake_y_motivo() -> None:
    """Valida que no se permita cuarentenar un test sin motivo ni ticket de desflake."""
    store = QuarantineStore()
    tenant_id = "tenant-discipline-01"
    test_id = "tests/unit/test_api.py::test_endpoint"

    # Falla sin motivo
    with pytest.raises(QuarantineValidationError) as exc1:
        store.quarantine_test(
            tenant_id=tenant_id,
            test_id=test_id,
            reason="",
            desflake_ticket="DESFLAKE-300",
        )
    assert "No se puede cuarentenar un test sin motivo tipificado" in str(exc1.value)

    # Falla con motivo en blanco
    with pytest.raises(QuarantineValidationError) as exc2:
        store.quarantine_test(
            tenant_id=tenant_id,
            test_id=test_id,
            reason="   ",
            desflake_ticket="DESFLAKE-300",
        )
    assert "No se puede cuarentenar un test sin motivo tipificado" in str(exc2.value)

    # Falla sin ticket de desflake
    with pytest.raises(QuarantineValidationError) as exc3:
        store.quarantine_test(
            tenant_id=tenant_id,
            test_id=test_id,
            reason="Inestable en red",
            desflake_ticket="",
        )
    assert "No se puede cuarentenar un test sin ticket de desflake asociado" in str(exc3.value)

    # Cuarentena válida con motivo y ticket
    record = store.quarantine_test(
        tenant_id=tenant_id,
        test_id=test_id,
        reason="Fallo esporádico por latencia",
        desflake_ticket="DESFLAKE-300",
        criterio_id="CRIT-LAT-01",
    )
    assert record.status == "active"
    assert record.reason == "Fallo esporádico por latencia"
    assert record.desflake_ticket == "DESFLAKE-300"
    assert record.criterio_id == "CRIT-LAT-01"


# ---------------------------------------------------------------------------
# Prueba 6: test_inmutabilidad_dtos_cuarentena
# ---------------------------------------------------------------------------
def test_inmutabilidad_dtos_cuarentena() -> None:
    """Valida que los DTOs oficiales de cuarentena sean inmutables (frozen=True)."""
    history_entry = FlakyHistoryEntryDTO(
        run_id="run-1",
        executed_at="2026-11-09T20:00:00Z",
        result="pass",
        duration_ms=12.5,
    )
    with pytest.raises(ValidationError):
        history_entry.result = "fail"

    record = QuarantineRecordDTO(
        tenant_id="tenant-1",
        test_identifier="test_case",
        criterio_id="CRIT-1",
        status="active",
        streak_count=3,
        flaky_count=2,
        reason="Flaky network",
        desflake_ticket="DESFLAKE-1",
        entered_at="2026-11-09T20:00:00Z",
        last_seen_at="2026-11-09T20:05:00Z",
        history=[history_entry],
    )
    with pytest.raises(ValidationError):
        record.status = "graduated"

    summary = RunFlakinessSummaryDTO(
        run_id="run-1",
        total_tests=50,
        flaky_tests_count=1,
        flakiness_rate=0.02,
        threshold_max=0.02,
        exceeded_threshold=False,
    )
    with pytest.raises(ValidationError):
        summary.total_tests = 100


# ---------------------------------------------------------------------------
# Prueba 7: test_aislamiento_multitenant
# ---------------------------------------------------------------------------
def test_aislamiento_multitenant() -> None:
    """Verifica que el estado de cuarentena de un tenant no contamine a otros tenants."""
    store = QuarantineStore()
    tenant_a = "tenant-alpha"
    tenant_b = "tenant-beta"
    shared_test_id = "tests/unit/test_shared.py::test_common_logic"

    # Cuarentenar en tenant_a
    store.quarantine_test(
        tenant_id=tenant_a,
        test_id=shared_test_id,
        reason="Falla en tenant alpha",
        desflake_ticket="DESFLAKE-ALPHA",
    )

    # Validar que está en cuarentena en tenant_a pero NO en tenant_b
    assert store.is_quarantined(tenant_a, shared_test_id) is True
    assert store.is_quarantined(tenant_b, shared_test_id) is False

    # Suite bloqueante en tenant_b debe conservar el test
    blocking_b = store.filter_blocking_suite(tenant_b, [shared_test_id])
    assert shared_test_id in blocking_b

    # Suite bloqueante en tenant_a debe excluirlo
    blocking_a = store.filter_blocking_suite(tenant_a, [shared_test_id])
    assert shared_test_id not in blocking_a


# ---------------------------------------------------------------------------
# Prueba 8: test_export_and_load_quarantine_report
# ---------------------------------------------------------------------------
def test_export_and_load_quarantine_report(tmp_path: Path) -> None:
    """Valida la exportación e importación WORM determinista en formato JSON."""
    store = QuarantineStore()
    tenant_id = "tenant-export-01"
    json_file = tmp_path / "reports" / "quarantine_report.json"

    store.quarantine_test(
        tenant_id=tenant_id,
        test_id="test_x",
        reason="Falla 1",
        desflake_ticket="DESFLAKE-X",
    )
    store.quarantine_test(
        tenant_id=tenant_id,
        test_id="test_y",
        reason="Falla 2",
        desflake_ticket="DESFLAKE-Y",
    )

    # Exportar reporte
    exported_path = store.export_quarantine_report(tenant_id, json_file)
    assert exported_path.exists()
    assert exported_path.is_file()

    # Cargar en un store nuevo y verificar restauración
    new_store = QuarantineStore()
    loaded_records = new_store.load_quarantine_report(exported_path)

    assert len(loaded_records) == 2
    assert new_store.is_quarantined(tenant_id, "test_x") is True
    assert new_store.is_quarantined(tenant_id, "test_y") is True

    # Cargar archivo inexistente debe fallar
    with pytest.raises(FileNotFoundError):
        new_store.load_quarantine_report(tmp_path / "non_existent.json")


# ---------------------------------------------------------------------------
# Prueba 9: test_retry_handler_hooks
# ---------------------------------------------------------------------------
def test_retry_handler_hooks() -> None:
    """Valida la integración con RetryHandler vía record_flaky y add_quarantine."""
    store = QuarantineStore()
    tenant_id = "tenant-retry-01"

    # Verificar hook directo record_flaky
    store.record_flaky(
        case_id="case_flaky_hook",
        tenant_id=tenant_id,
        error_summary="Timeout en intento 1",
    )
    assert store.is_quarantined(tenant_id, "case_flaky_hook") is False

    # Verificar hook directo add_quarantine
    store.add_quarantine(
        case_id="case_manual_hook",
        reason="Aislamiento manual desde hook",
        tenant_id=tenant_id,
    )
    assert store.is_quarantined(tenant_id, "case_manual_hook") is True

    # Verificar integración completa pasando store a FlakyRetryHandler
    handler = FlakyRetryHandler(
        config=RetryConfigDTO(max_retries=1, delay_between_retries_sec=0.0),
        quarantine_store=store,
    )

    # Función que falla intento 1 y aprueba en intento 2 -> FLAKY
    call_count = 0

    def flaky_func() -> tuple[bool, str | None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False, "Conexión rechazada intento 1"
        return True, None

    res = handler.execute_with_retry(
        case_id="test_integration_flaky",
        executable_fn=flaky_func,
        tenant_id=tenant_id,
    )
    assert res.final_status == "flaky"
    # El hook de record_flaky debe haber registrado la ejecución en el store
    records = store._get_tenant_records(tenant_id)
    assert "test_integration_flaky" in records
    assert records["test_integration_flaky"].flaky_count >= 1


# ---------------------------------------------------------------------------
# Prueba 10: test_edge_cases_y_ventana_observacion
# ---------------------------------------------------------------------------
def test_edge_cases_y_ventana_observacion() -> None:
    """Valida casos borde: ventana de observación truncada, total negativo y total cero."""
    store = QuarantineStore(observation_window=3)
    tenant_id = "tenant-edge-01"
    test_id = "test_window"

    for i in range(5):
        store.register_execution(
            tenant_id=tenant_id,
            test_id=test_id,
            criterio_id="CRIT-WINDOW",
            result="pass",
            run_id=f"run-{i}",
        )

    record = store._get_tenant_records(tenant_id)[test_id]
    # La ventana solo debe retener las últimas 3 ejecuciones
    assert len(record.history) == 3
    assert [entry.run_id for entry in record.history] == ["run-2", "run-3", "run-4"]

    # Total tests == 0 no divide por cero
    summary_zero = store.evaluate_run_flakiness(total_tests=0, flaky_tests=[])
    assert summary_zero.flakiness_rate == 0.0

    # Total tests negativo lanza ValueError
    with pytest.raises(ValueError) as exc:
        store.evaluate_run_flakiness(total_tests=-5, flaky_tests=[])
    assert "El total de pruebas no puede ser negativo" in str(exc.value)
