"""KC-BE-05-7: configuración de Blob e identidad administrada."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kallicode_api.servicios import blob
from kallicode_core.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://localhost:10000/devstoreaccount1",
        "https://localhost/cuenta",
        "https://127.0.0.1/cuenta",
    ],
)
def test_produccion_rechaza_direccion_blob_invalida(url):
    settings = Settings.model_construct(
        env="production",
        blob_account_url=url,
    )

    with pytest.raises(ValueError, match="KC_BLOB_ACCOUNT_URL"):
        settings.validar_blob()


def test_produccion_acepta_direccion_https():
    settings = Settings.model_construct(
        env="production",
        blob_account_url="https://cuenta.blob.core.windows.net",
    )

    assert settings.validar_blob() is settings


def test_local_puede_usar_azurite():
    settings = Settings.model_construct(
        env="local",
        blob_account_url="http://localhost:10000/devstoreaccount1",
    )

    assert settings.validar_blob() is settings


@pytest.mark.parametrize(
    ("operacion", "permiso"),
    [
        ("subida", "c"),
        ("descarga", "r"),
    ],
)
def test_azure_firma_con_clave_de_delegacion(
    monkeypatch,
    operacion,
    permiso,
):
    settings = SimpleNamespace(
        env="production",
        blob_account_url="https://cuenta.blob.core.windows.net",
        blob_container_evidencia="evidencia",
        blob_managed_identity_client_id=None,
    )
    monkeypatch.setattr(blob, "get_settings", lambda: settings)

    credencial = MagicMock()
    fabrica_credencial = MagicMock(return_value=credencial)
    monkeypatch.setattr(blob, "ManagedIdentityCredential", fabrica_credencial)

    cliente = MagicMock()
    cliente.account_name = "cuenta"
    cliente.get_user_delegation_key.return_value = "clave-temporal"
    cliente.get_blob_client.return_value.url = (
        "https://cuenta.blob.core.windows.net/evidencia/archivo.txt"
    )

    fabrica_cliente = MagicMock()
    fabrica_cliente.return_value.__enter__.return_value = cliente
    monkeypatch.setattr(blob, "BlobServiceClient", fabrica_cliente)

    firmar = MagicMock(return_value="sig=firma")
    monkeypatch.setattr(blob, "generate_blob_sas", firmar)

    if operacion == "subida":
        url, _ = blob.url_subida("archivo.txt", "text/plain")
    else:
        url, _ = blob.url_descarga("archivo.txt")

    fabrica_credencial.assert_called_once_with(client_id=None)
    cliente.get_user_delegation_key.assert_called_once()
    assert firmar.call_args.kwargs["user_delegation_key"] == "clave-temporal"
    assert firmar.call_args.kwargs["permission"] == permiso
    assert url.endswith("?sig=firma")
    credencial.close.assert_called_once()


@pytest.mark.parametrize("fallar", [False, True])
def test_verificar_delegacion_cierra_credencial(monkeypatch, fallar):
    settings = SimpleNamespace(
        blob_account_url="https://cuenta.blob.core.windows.net",
        blob_managed_identity_client_id="",
    )
    monkeypatch.setattr(blob, "get_settings", lambda: settings)

    credencial = MagicMock()
    fabrica_credencial = MagicMock(return_value=credencial)
    monkeypatch.setattr(blob, "ManagedIdentityCredential", fabrica_credencial)

    cliente = MagicMock()
    if fallar:
        cliente.get_user_delegation_key.side_effect = RuntimeError("Azure no disponible")

    fabrica_cliente = MagicMock()
    fabrica_cliente.return_value.__enter__.return_value = cliente
    monkeypatch.setattr(blob, "BlobServiceClient", fabrica_cliente)

    if fallar:
        with pytest.raises(RuntimeError, match="Azure no disponible"):
            blob.verificar_delegacion()
    else:
        blob.verificar_delegacion()

    fabrica_credencial.assert_called_once_with(client_id=None)
    cliente.get_user_delegation_key.assert_called_once()
    credencial.close.assert_called_once()
