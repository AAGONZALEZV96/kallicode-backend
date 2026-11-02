"""Pruebas de validación HMAC de webhooks (BE-F05)."""

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from kallicode_api.routers.webhooks import _verificar_firma_y_conexion
from kallicode_core.errors import AppError


@pytest.mark.asyncio
async def test_webhook_hmac_valido_usando_boveda():
    # Setup de prueba
    cuerpo = b'{"evento": "ticket.creado"}'
    clave_secreta = "secreto_super_seguro"
    firma_valida = hmac.new(clave_secreta.encode(), cuerpo, hashlib.sha256).hexdigest()

    mock_request = MagicMock(spec=Request)
    mock_request.body.return_value = cuerpo

    # Mockeamos la BD para que devuelva una conexión válida sin secreto
    mock_cn = {"id": "cn-123", "tenant_id": "t-1", "configuracion": {}}

    # Parcheamos la DB, Bóveda y el Rate Limiter
    with (
        patch("kallicode_api.routers.webhooks.uno") as mock_uno,
        patch("kallicode_api.servicios.boveda.leer") as mock_boveda,
        patch("kallicode_core.comercial.rl_webhook"),
    ):
        mock_uno.return_value = mock_cn
        mock_boveda.return_value = {"webhook_secret": clave_secreta}

        # Ejecutamos la función (No debe lanzar AppError)
        cn, cuerpo_res, payload = await _verificar_firma_y_conexion(
            proveedor="help",
            token="token-abc",
            request=mock_request,
            firma=f"sha256={firma_valida}",
        )

        assert cn["id"] == "cn-123"
        assert payload["evento"] == "ticket.creado"


@pytest.mark.asyncio
async def test_webhook_hmac_invalido_rechazado():
    cuerpo = b'{"evento": "ticket.creado"}'
    clave_secreta = "secreto_super_seguro"

    mock_request = MagicMock(spec=Request)
    mock_request.body.return_value = cuerpo
    mock_cn = {"id": "cn-123", "tenant_id": "t-1", "configuracion": {}}

    with (
        patch("kallicode_api.routers.webhooks.uno") as mock_uno,
        patch("kallicode_api.servicios.boveda.leer") as mock_boveda,
        patch("kallicode_core.comercial.rl_webhook"),
    ):
        mock_uno.return_value = mock_cn
        mock_boveda.return_value = {"webhook_secret": clave_secreta}

        # Ejecutamos con firma incorrecta, debe lanzar 401
        with pytest.raises(AppError) as exc_info:
            await _verificar_firma_y_conexion(
                proveedor="help",
                token="token-abc",
                request=mock_request,
                firma="sha256=firma_falsa_inventada",
            )

        # Validamos que el error contenga el código correcto, ignorando el formato
        assert "FIRMA_INVALIDA" in str(exc_info.value)
