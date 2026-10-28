from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from kallicode_api.routers.auth import LoginIn, login, obtener_ip_real
from kallicode_core.logging import hash_email

# --- 1. Prueba unitaria de la extracción de IP ---


def test_obtener_ip_real_con_proxy():
    """Verifica que se extraiga la primera IP del header X-Forwarded-For."""
    scope = {
        "type": "http",
        "client": ("10.0.0.5", 8000),  # IP del balanceador de carga interno
        "headers": [
            (b"x-forwarded-for", b"200.190.10.5, 10.0.0.5")  # La real es la primera
        ],
    }
    req = Request(scope)
    assert obtener_ip_real(req) == "200.190.10.5"


def test_obtener_ip_real_sin_proxy():
    """Si no hay proxy, debe usar la IP directa del cliente."""
    scope = {"type": "http", "client": ("192.168.1.100", 8000), "headers": []}
    req = Request(scope)
    assert obtener_ip_real(req) == "192.168.1.100"


# --- 2. Prueba de integración del bloqueo en Redis ---


@pytest.mark.asyncio
@patch("kallicode_api.routers.auth.comercial.redis_cliente")
@patch("kallicode_api.routers.auth.todos")
@patch("kallicode_api.routers.auth.comercial.rl_login")
async def test_login_bloqueo_usa_llave_compuesta(mock_rl_login, mock_todos, mock_redis_cliente):
    """Verifica que Redis use el par (email + IP) para contar fallos y bloquear."""

    # Preparamos el mock de Redis
    mock_redis = AsyncMock()
    mock_redis_cliente.return_value = mock_redis

    mock_redis.exists.return_value = False  # Aún no está bloqueado
    mock_redis.incr.return_value = 5  # Simulamos que es el 5to intento fallido

    # Simulamos que la DB no encuentra el usuario para forzar el error de credenciales
    mock_todos.return_value = []

    # Petición simulada de un atacante detrás de un proxy
    ip_atacante = "190.168.1.5"
    correo_atacado = "admin@kallicode.com"
    req = Request(
        {
            "type": "http",
            "client": ("10.0.0.1", 80),
            "headers": [(b"x-forwarded-for", ip_atacante.encode("utf-8"))],
        }
    )
    datos = LoginIn(email=correo_atacado, password="password_incorrecta")

    # Ejecutamos el login (debe fallar con una excepción HTTP 401)
    with pytest.raises(Exception) as excinfo:
        await login(datos, req)

    assert excinfo.type.__name__ == "AppError"
    assert "Correo o contraseña" in str(excinfo.value.args)

    # Comprobamos cómo se construyeron las llaves en Redis
    hash_correo = hash_email(correo_atacado)
    clave_fallos_esperada = f"login:fallos:{hash_correo}:{ip_atacante}"
    clave_bloqueo_esperada = f"login:bloqueo:{hash_correo}:{ip_atacante}"

    # 1. ¿Contó el fallo usando la IP y el correo?
    mock_redis.incr.assert_called_with(clave_fallos_esperada)

    # 2. ¿Al llegar a 5 fallos, creó el bloqueo con la llave compuesta por 15 min (900s)?
    mock_redis.setex.assert_called_with(clave_bloqueo_esperada, 900, "1")
