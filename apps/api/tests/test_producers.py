from unittest.mock import AsyncMock, patch

import pytest
from kallicode_api.routers.auth import ForgotIn, forgot_password


@pytest.mark.asyncio
@patch("kallicode_api.routers.auth.todos")  # <-- Cambiamos 'uno' por 'todos'
@patch("kallicode_api.routers.auth.sesion_sistema")
@patch("kallicode_core.comercial.redis_cliente")
async def test_forgot_password_xadd_uses_maxlen(mock_redis_cliente, mock_sesion, mock_todos):
    # 1. Preparar los mocks
    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 1  # Simulamos que no ha superado el rate limit
    mock_redis_cliente.return_value = mock_redis

    # Simulamos que el correo existe en 1 tenant (todos() devuelve una lista)
    mock_todos.return_value = [{"id": "u_123", "tenant_id": "t_456"}]

    # 2. Ejecutar el endpoint directamente
    datos = ForgotIn(email="test@kallicode.com")
    await forgot_password(datos)

    # 3. Verificar que r.xadd fue llamado con las protecciones correctas
    assert mock_redis.xadd.call_count == 1
    args, kwargs = mock_redis.xadd.call_args

    # Verificamos el stream
    assert args[0] == "kc:correo"

    # Verificamos los parámetros de memoria exigidos por el ticket BE-F04
    assert kwargs.get("maxlen") == 10000
    assert kwargs.get("approximate") == True

    # Verificamos el payload básico
    payload = args[1]
    assert payload["tipo"] == "reset_password"
    assert payload["user_id"] == "u_123"
