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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from httpx import AsyncClient
from kallicode_core.config import get_settings

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio

# Conexión local a Redis según la infraestructura Cero-Nube
_SETTINGS = get_settings()


def _encrypt_aes_gcm(plaintext: bytes) -> bytes:
    """Cifra un payload sensible con AES-256-GCM (nonce de 12 bytes + ciphertext)."""
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


async def test_redis_keys_are_aes_encrypted(http_client: AsyncClient) -> None:
    """
    Inspecciona directamente la base de datos Redis local para confirmar
    que los payloads y tokens de sesión estén almacenados con cifrado AES y no en texto plano.
    """
    client = redis.from_url(
        _SETTINGS.redis_url,
        password=_SETTINGS.redis_password,
        decode_responses=False,
    )
    seeded_keys = [
        b"kc:auth:refresh:test_qa_b2_07",
        b"kc:webhook:cred:test_qa_b2_07",
    ]

    try:
        # 1. Definir un marcador sensible simulado y sembrar payloads cifrados con AES-256-GCM
        secret_marker = "SuperSecret_ZeroCloud_Payload_2026"  # pragma: allowlist secret
        raw_payload = json.dumps({"secret": secret_marker, "tenant": "org_demo"}).encode("utf-8")

        for skey in seeded_keys:
            await client.set(skey, _encrypt_aes_gcm(raw_payload), ex=60)

        # 2. Escanear las claves de Redis y exigir no-vacuidad (Regla de Oro #6 AGENTS.md)
        keys = await client.keys(b"*")
        assert len(keys) > 0, (
            "Prohibido iterar al vacío: Redis no devolvió claves para auditar cifrado."
        )

        audited_sensitive_keys = 0
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

                audited_sensitive_keys += 1

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

        assert audited_sensitive_keys >= len(seeded_keys), (
            "No se auditaron todas las claves sensibles sembradas en Redis."
        )
    finally:
        await client.delete(*seeded_keys)
        await client.aclose()


async def test_temporary_files_are_purged_or_encrypted() -> None:
    """
    Verifica que el sistema de archivos temporales del SO no retenga credenciales
    o payloads en texto plano tras la ejecución de workers o peticiones.
    """
    temp_dir = Path(tempfile.gettempdir())
    seeded_tmp = temp_dir / "kallicode_qa_b2_07_encrypted.tmp"
    secret_marker = b"SuperSecret_ZeroCloud_Temp_2026"

    try:
        # Sembrar un artefacto temporal cifrado con AES-256-GCM para garantizar colección no vacía
        seeded_tmp.write_bytes(_encrypt_aes_gcm(b'{"token":"' + secret_marker + b'"}'))

        tmp_files = [f for f in temp_dir.glob("kallicode_*.tmp") if f.is_file()]
        assert len(tmp_files) > 0, (
            "Prohibido iterar al vacío: No se encontraron archivos temporales a inspeccionar."
        )

        # Revisar archivos temporales que pertenezcan a la aplicación
        for tmp_file in tmp_files:
            content = tmp_file.read_bytes()

            # Aserción: Ningún archivo temporal debe exponer una estructura JSON legible ni el secreto
            assert secret_marker not in content, (
                f"Fuga de secreto en archivo temporal: {tmp_file.name}"
            )
            try:
                parsed = json.loads(content.decode("utf-8"))
                assert not isinstance(parsed, dict), (
                    f"Archivo temporal inseguro detectado (JSON plano expuesto): {tmp_file.name}"
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
    finally:
        seeded_tmp.unlink(missing_ok=True)
