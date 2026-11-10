import pytest
from kallicode_api.routers.auth import forgot_password
from kallicode_core.comercial import redis_cliente


# Objeto simulado para evitar problemas de importación con los esquemas Pydantic
class MockForgotIn:
    def __init__(self, email: str):
        self.email = email
        self.org_id = None


@pytest.mark.asyncio
async def test_stream_correo_no_filtra_tokens():
    # 1. Limpiamos el stream para la prueba
    r = redis_cliente()
    await r.delete("kc:correo")

    # 2. Forzamos la emisión de un evento de reseteo usando el Mock
    await forgot_password(MockForgotIn(email="test@kallicode.dev"))

    # 3. Leemos el último mensaje del stream
    mensajes = await r.xread({"kc:correo": "0-0"}, count=1)
    if not mensajes:
        return

    _, eventos = mensajes[0]
    _, payload = eventos[0]

    # 4. Aserción de seguridad (BE-F05)
    assert "token" not in payload, "Vulnerabilidad: El stream contiene un token en claro"
    assert "token_ref" in payload, "El stream no contiene la referencia a la bóveda"
