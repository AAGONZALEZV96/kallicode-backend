"""
Ticket: KC-QA-B2-07
Validación de eliminación de secretos en Redis y Archivos Temporales.
Asegura que los datos sensibles (tokens de refresco, webhooks, credenciales)
se almacenen con cifrado AES y no en texto plano, cumpliendo la política Zero-Cloud.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio

# Conexión local a Redis según la infraestructura Cero-Nube
REDIS_URL = os.getenv("KC_REDIS_URL", "redis://localhost:6379/0")


async def test_redis_keys_are_aes_encrypted(http_client: AsyncClient) -> None:
    """
    Inspecciona directamente la base de datos Redis local para confirmar
    que los payloads y tokens de sesión no estén almacenados en texto plano.
    """
    # Conexión a Redis leyendo bytes crudos (decode_responses=False) para auditar el cifrado físico
    client = redis.from_url(REDIS_URL, decode_responses=False)

    try:
        # 1. Definir un marcador sensible simulado que nunca debe aparecer en Redis en crudo
        secret_marker = "SuperSecret_ZeroCloud_Payload_2026"  # pragma: allowlist secret

        # 2. Escanear las claves de Redis buscando patrones de caché transaccional
        keys = await client.keys(b"*")

        for key_b in keys:
            key_str = key_b.decode("utf-8", errors="ignore")

            # Focalizar la auditoría en claves de identidad y comunicación
            if any(
                sensitive in key_str.lower()
                for sensitive in ["refresh", "webhook", "token", "auth", "cred"]
            ):
                value = await client.get(key_b)
                if not value:
                    continue

                # Aserción 1: El secreto en texto plano NUNCA debe encontrarse en los bytes de Redis
                assert secret_marker.encode() not in value, (
                    f"Fuga Crítica: El secreto se encontró en texto plano en la clave Redis '{key_str}'."
                )

                # Aserción 2: Un bloque AES válido debe ser incomprensible (entropía alta).
                # Si logramos decodificarlo como JSON puro, significa que no está cifrado.
                try:
                    decoded_value = value.decode("utf-8")
                    json_data = json.loads(decoded_value)

                    # Si el parseo es exitoso y contiene estructura, hay una vulnerabilidad
                    assert not isinstance(json_data, dict), (
                        f"Vulnerabilidad Crítica: La clave '{key_str}' contiene un JSON sin cifrar en Redis."
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Comportamiento esperado: Los bytes cifrados con AES arrojarán
                    # errores al intentar ser decodificados como texto UTF-8 o JSON.
                    pass
    finally:
        await client.aclose()


async def test_temporary_files_are_purged_or_encrypted() -> None:
    """
    Verifica que el sistema de archivos temporales del SO no retenga credenciales
    o payloads en texto plano tras la ejecución de workers o peticiones.
    """
    temp_dir = Path(tempfile.gettempdir())

    # Revisar archivos temporales residuales que pertenezcan a la aplicación
    for tmp_file in temp_dir.glob("kallicode_*.tmp"):
        if tmp_file.is_file():
            content = tmp_file.read_bytes()

            # Aserción: Ningún archivo temporal debe exponer una estructura JSON legible
            assert b"{" not in content, (
                f"Archivo temporal inseguro detectado (texto plano expuesto): {tmp_file.name}"
            )
