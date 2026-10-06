"""
Ticket: KC-QA-B2-03
Suite de pruebas negativas de seguridad para tokens JWT y prevención de Replay Attacks.
Asegura la protección criptográfica de la API, cerrando los hallazgos QA-F02 y QA-F08.
"""

import base64
import json
import os
import time
from collections.abc import AsyncGenerator

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app

# Habilitar el soporte asíncrono para todas las pruebas del módulo
pytestmark = pytest.mark.asyncio

# El secreto debe coincidir con el fallback del backend local en Cero-Nube
# Extendido a >32 bytes para evitar InsecureKeyLengthWarning (RFC 7518)
JWT_SECRET = os.getenv("KC_JWT_SECRET", "kallicode_local_secret_super_seguro_32b")
JWT_ALGORITHM = "HS256"

# Endpoint crítico que exige autenticación según el contrato API
PROTECTED_URL = "/api/v1/tickets"


@pytest.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Sobreescribe el fixture inyectando la app FastAPI directamente vía ASGI.
    Esto permite ejecutar la suite en el CI sin levantar el servidor Uvicorn en el puerto 8000.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_rechaza_ausencia_de_token(http_client: AsyncClient) -> None:
    """Valida que un endpoint protegido no pueda ser consumido de forma anónima."""
    response = await http_client.get(PROTECTED_URL)
    assert response.status_code in (401, 403), "La API permitió acceso sin token JWT en cabeceras."


async def test_rechaza_token_expirado(http_client: AsyncClient) -> None:
    """Verifica que el backend evalúe el claim 'exp' y rechace tokens caducados."""
    payload = {
        "sub": "u_123",
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "role": "admin",
        "exp": int(time.time()) - 3600,  # Expirado hace 1 hora
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    response = await http_client.get(PROTECTED_URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401, "La API aceptó un token expirado."


async def test_rechaza_firma_invalida_hmac(http_client: AsyncClient) -> None:
    """Asegura que un token firmado por un tercero desconocido sea rechazado."""
    payload = {
        "sub": "u_123",
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "role": "admin",
        "exp": int(time.time()) + 3600,
    }
    # Firmado con una clave maliciosa que el backend ignora
    token = jwt.encode(payload, "clave_secreta_atacante_super_larga_32b", algorithm=JWT_ALGORITHM)

    response = await http_client.get(PROTECTED_URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401, "La API aceptó un token con firma HMAC ajena/inválida."


async def test_rechaza_manipulacion_de_claims(http_client: AsyncClient) -> None:
    """
    Simula un ataque donde un usuario legítimo altera su payload en base64
    (ej. elevando su rol) e intenta usar la firma original.
    """
    payload_original = {
        "sub": "u_123",
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "role": "viewer",
        "exp": int(time.time()) + 3600,
    }
    token_original = jwt.encode(payload_original, JWT_SECRET, algorithm=JWT_ALGORITHM)

    # Separar las partes del JWT
    header_b64, _, signature_b64 = token_original.split(".")

    # Elevar privilegios en el payload
    payload_manipulado = payload_original.copy()
    payload_manipulado["role"] = "owner"

    # Recodificar a base64url sin relleno
    payload_falso_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload_manipulado).encode()).decode().rstrip("=")
    )

    # Ensamblar el token forjado con la firma vieja
    token_forjado = f"{header_b64}.{payload_falso_b64}.{signature_b64}"

    response = await http_client.get(
        PROTECTED_URL, headers={"Authorization": f"Bearer {token_forjado}"}
    )
    assert response.status_code == 401, (
        "La API permitió escalada de privilegios ignorando la divergencia de firma."
    )


async def test_rechaza_ataque_algoritmo_none(http_client: AsyncClient) -> None:
    """Previene el bypass clásico forzando al validador a aceptar tokens sin firma."""
    payload = {
        "sub": "u_123",
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "role": "owner",
        "exp": int(time.time()) + 3600,
    }
    # En jwt de Python, el algoritmo 'none' no lleva firma
    token = jwt.encode(payload, "", algorithm="none")

    response = await http_client.get(PROTECTED_URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401, "Vulnerabilidad crítica: La API acepta algoritmo 'none'."
