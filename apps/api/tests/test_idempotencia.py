from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from kallicode_api.idempotencia import con_idempotencia


@pytest.mark.anyio
async def test_reserva_exitosa_y_memorizacion():
    db = AsyncMock()
    # 1. Reserva devuelve clave
    mock_res_reserva = MagicMock()
    mock_res_reserva.first.return_value = ("clave-123",)

    db.execute.side_effect = [mock_res_reserva, AsyncMock()]

    logica_mock = AsyncMock(return_value={"resultado": "ok"})

    resp = await con_idempotencia(
        db=db,
        tenant_id="tenant-test",
        endpoint_name="test_endpoint",
        idempotency_key="clave-123",
        codigo_exito=201,
        logica=logica_mock,
    )

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 201
    assert logica_mock.await_count == 1


@pytest.mark.anyio
async def test_conflicto_concurrente_status_cero():
    db = AsyncMock()
    # 1. Reserva retorna None (conflicto)
    mock_res_reserva = MagicMock()
    mock_res_reserva.first.return_value = None

    # 2. SELECT retorna status_code 0 (en progreso)
    mock_res_memo = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.first.return_value = {"respuesta": {}, "status_code": 0}
    mock_res_memo.mappings.return_value = mock_mappings

    db.execute.side_effect = [mock_res_reserva, mock_res_memo]

    logica_mock = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await con_idempotencia(
            db=db,
            tenant_id="tenant-test",
            endpoint_name="test_endpoint",
            idempotency_key="clave-123",
            codigo_exito=200,
            logica=logica_mock,
        )

    assert exc_info.value.status_code == 409
    assert logica_mock.await_count == 0


@pytest.mark.anyio
async def test_reintento_devuelve_respuesta_guardada():
    db = AsyncMock()
    mock_res_reserva = MagicMock()
    mock_res_reserva.first.return_value = None

    mock_res_memo = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.first.return_value = {
        "respuesta": {"job_id": "123"},
        "status_code": 201,
    }
    mock_res_memo.mappings.return_value = mock_mappings

    db.execute.side_effect = [mock_res_reserva, mock_res_memo]

    logica_mock = AsyncMock()

    resp = await con_idempotencia(
        db=db,
        tenant_id="tenant-test",
        endpoint_name="test_endpoint",
        idempotency_key="clave-123",
        codigo_exito=201,
        logica=logica_mock,
    )

    assert resp.status_code == 201
    assert logica_mock.await_count == 0


@pytest.mark.anyio
async def test_rollback_clave_si_falla_logica():
    db = AsyncMock()
    mock_res_reserva = MagicMock()
    mock_res_reserva.first.return_value = ("clave-123",)

    db.execute.side_effect = [mock_res_reserva, AsyncMock()]

    logica_falla = AsyncMock(side_effect=RuntimeError("Fallo inesperado"))

    with pytest.raises(RuntimeError):
        await con_idempotencia(
            db=db,
            tenant_id="tenant-test",
            endpoint_name="test_endpoint",
            idempotency_key="clave-123",
            codigo_exito=201,
            logica=logica_falla,
        )

    # Verificar que el último execute ejecutó el DELETE
    ultimo_query = str(db.execute.call_args[0][0])
    assert "DELETE FROM core.idempotency_keys" in ultimo_query
