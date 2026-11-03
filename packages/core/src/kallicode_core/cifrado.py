"""Motor criptográfico central de Kallicode.
Implementa AES-GCM (Authenticated Encryption with Associated Data).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kallicode_core.logging import log


def cifrar(texto: str, clave_hex: str) -> str:
    """Cifra un texto en claro usando AES-GCM de 256 bits."""
    try:
        clave = bytes.fromhex(clave_hex)
        aesgcm = AESGCM(clave)
        nonce = os.urandom(12)  # El nonce debe ser único por cifrado, 12 bytes estándar
        ct = aesgcm.encrypt(nonce, texto.encode("utf-8"), None)
        # Empaquetamos nonce + ciphertext en Base64 para guardarlo como string seguro
        return base64.b64encode(nonce + ct).decode("utf-8")
    except ValueError as e:
        log.error("cifrado.error_hex", error=str(e))
        raise RuntimeError("La clave de cifrado no es un hexadecimal válido.") from e


def descifrar(cifrado_b64: str, clave_hex: str) -> str:
    """Descifra un texto generado por `cifrar`. Fallará si el texto fue manipulado."""
    clave = bytes.fromhex(clave_hex)
    aesgcm = AESGCM(clave)

    try:
        data = base64.b64decode(cifrado_b64)
        nonce, ct = data[:12], data[12:]
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    except Exception as e:
        log.error("cifrado.error_descifrado", error=str(e))
        raise ValueError("Fallo de integridad o cifrado corrupto al descifrar.") from e
