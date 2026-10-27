"""
Prueba unitaria para el Mock de Bóveda de Secretos Local (KC-QA-B2-04).
Verifica que los secretos se cifren físicamente en disco y no se filtren en texto plano.
"""

import json
import os
import tempfile

from kallicode_testing.secrets_mock import LocalKeyVaultMock


def test_mock_vault_cifrado_y_recuperacion():
    # 1. Inicializar la bóveda con llave dinámica en memoria
    vault = LocalKeyVaultMock()

    # 2. Guardar un secreto de prueba
    vault.set_secret("KC_DB_PASSWORD", "SuperSecreto_ZeroCloud_2026!")

    # 3. Recuperación en memoria (transparente para la aplicación)
    recuperado = vault.get_secret("KC_DB_PASSWORD")
    assert recuperado == "SuperSecreto_ZeroCloud_2026!", (
        "Fallo al descifrar o recuperar el secreto."
    )

    # 4. Auditoría de disco: Verificar que el archivo físico NO contenga texto plano
    store_path = os.path.join(tempfile.gettempdir(), "kallicode_vault_mock.json")
    with open(store_path, "r") as f:
        data = json.load(f)

    assert "KC_DB_PASSWORD" in data, "La llave no se guardó en el archivo JSON."
    assert data["KC_DB_PASSWORD"]["ciphertext"] != "SuperSecreto_ZeroCloud_2026!", (
        "Vulnerabilidad: El secreto se expuso en texto plano."
    )
    assert "nonce" in data["KC_DB_PASSWORD"], "Falta el nonce criptográfico en el archivo."

    # 5. Probar comportamiento con llaves inexistentes
    assert vault.get_secret("NO_EXISTE") is None, (
        "Debería retornar None para llaves no registradas."
    )

    # 6. Limpieza segura (Purge)
    vault.purge_vault()
    assert not os.path.exists(store_path), "La función purge_vault no destruyó el archivo temporal."
