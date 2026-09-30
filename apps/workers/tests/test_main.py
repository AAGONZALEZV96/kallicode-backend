from unittest.mock import AsyncMock

import pytest

# Importamos la función que refactorizamos en main.py
from kallicode_workers.main import procesar_mensaje


@pytest.mark.asyncio
async def test_worker_failing_handler_goes_to_dlq():
    # 1. Preparación de los mocks
    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 1  # Por defecto, simulamos el primer intento

    stream = "kc:stream:users"
    mid = b"1620000000000-0"  # Redis suele devolver los IDs como bytes
    mid_str = mid.decode("utf-8")
    campos = {"user_id": "123", "action": "invite"}
    retry_key = f"kc:retries:{stream}:{mid_str}"

    # Nuestro handler malicioso que siempre falla
    async def failing_handler(data):
        raise ValueError("Error simulado en el handler")

    # --- INTENTO 1 ---
    await procesar_mensaje(mock_redis, stream, mid, campos, failing_handler)

    # Verificamos que aumentó el contador pero NO hizo xack ni xadd
    mock_redis.incr.assert_called_with(retry_key)
    mock_redis.expire.assert_called_with(retry_key, 86400)
    mock_redis.xack.assert_not_called()
    mock_redis.xadd.assert_not_called()

    # --- INTENTO 2 ---
    mock_redis.incr.return_value = 2
    await procesar_mensaje(mock_redis, stream, mid, campos, failing_handler)

    # Sigue sin hacer xack ni enviarlo a DLQ
    assert mock_redis.xack.call_count == 0
    assert mock_redis.xadd.call_count == 0

    # --- INTENTO 3 (Límite, va a la DLQ) ---
    mock_redis.incr.return_value = 3
    await procesar_mensaje(mock_redis, stream, mid, campos, failing_handler)

    # 1. Debe haber enviado a la DLQ
    assert mock_redis.xadd.call_count == 1
    args, kwargs = mock_redis.xadd.call_args

    assert args[0] == "kc:dlq"
    assert kwargs["maxlen"] == 5000
    assert kwargs["approximate"] == True

    payload_dlq = args[1]
    assert "Error simulado en el handler" in payload_dlq["error"]
    assert payload_dlq["original_stream"] == stream

    # 2. Ahora SÍ debe hacer xack en el stream original para sacarlo de la PEL
    # Nota: _GRUPO en main.py está definido como "kc-workers"
    mock_redis.xack.assert_called_once_with(stream, "kc-workers", mid)

    # 3. Debe limpiar la clave de reintentos
    mock_redis.delete.assert_called_once_with(retry_key)
