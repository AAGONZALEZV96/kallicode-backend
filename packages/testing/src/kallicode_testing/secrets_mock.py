"""
Ticket: KC-QA-B2-04
Mock de Bóveda de Secretos Local (Zero-Cloud).
Simula la interfaz de Azure Key Vault utilizando cifrado AES-GCM en memoria
para almacenamiento seguro de secretos durante el entorno de pruebas local.
"""

import json
import os
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class LocalKeyVaultMock:
    def __init__(self, master_key_hex: str | None = None) -> None:
        """
        Inicializa el mock de Key Vault.
        Si no se provee master_key_hex, genera una llave dinámica efímera,
        garantizando que el archivo local sea indescifrable al terminar la sesión.
        """
        if master_key_hex:
            self._key = bytes.fromhex(master_key_hex)
        else:
            self._key = AESGCM.generate_key(bit_length=256)

        self._aesgcm = AESGCM(self._key)

        # Guardamos los secretos cifrados en un archivo temporal
        self._store_path = os.path.join(tempfile.gettempdir(), "kallicode_vault_mock.json")
        self._ensure_store()

    def _ensure_store(self) -> None:
        """Crea el archivo JSON base si no existe."""
        if not os.path.exists(self._store_path):
            with open(self._store_path, "w") as f:
                json.dump({}, f)

    def set_secret(self, secret_name: str, secret_value: str) -> None:
        """
        Cifra y almacena un secreto.
        Emula: SecretClient.set_secret(secret_name, secret_value)
        """
        nonce = os.urandom(12)  # AES-GCM requiere un nonce único de 12 bytes
        ciphertext = self._aesgcm.encrypt(nonce, secret_value.encode("utf-8"), None)

        with open(self._store_path, "r") as f:
            store = json.load(f)

        store[secret_name] = {"nonce": nonce.hex(), "ciphertext": ciphertext.hex()}

        with open(self._store_path, "w") as f:
            json.dump(store, f)

    def get_secret(self, secret_name: str) -> str | None:
        """
        Recupera y descifra un secreto almacenado.
        Emula: SecretClient.get_secret(secret_name).value
        """
        with open(self._store_path, "r") as f:
            store = json.load(f)

        if secret_name not in store:
            return None

        data = store[secret_name]
        nonce = bytes.fromhex(data["nonce"])
        ciphertext = bytes.fromhex(data["ciphertext"])

        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception:
            return None

    def purge_vault(self) -> None:
        """Limpia el almacenamiento local (ideal para fixtures de teardown)."""
        if os.path.exists(self._store_path):
            os.remove(self._store_path)


# Instancia global para inyección de dependencias en el módulo testing
local_vault = LocalKeyVaultMock()
