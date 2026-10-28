"""Pruebas para KC-DB-05-6: Campos de red en audit.audit_events."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from kallicode_core.auditoria import registrar_evento


@pytest.mark.asyncio
async def test_registrar_evento_con_contexto_de_red() -> None:
    mock_db = AsyncMock()
    req_id = str(uuid.uuid4())
    test_ip = "192.168.1.100"
    test_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

    with patch("kallicode_core.auditoria.uno", new_callable=AsyncMock) as mock_uno:
        mock_uno.return_value = {"sello": "a" * 64}

        sello = await registrar_evento(
            mock_db,
            tenant_id="tenant_demo",
            evento="ticket_creado",
            resumen="Nuevo ticket de prueba",
            actor_tipo="humano",
            actor_id="user_123",
            ip=test_ip,
            user_agent=test_ua,
            request_id=req_id,
        )

        assert sello == "a" * 64
        mock_uno.assert_called_once()
        _, kwargs = mock_uno.call_args
        params = kwargs.get("params") or mock_uno.call_args[0][2]
        assert params["ip"] == test_ip
        assert params["ua"] == test_ua
        assert params["rid"] == req_id


@pytest.mark.asyncio
async def test_registrar_evento_sin_contexto_de_red_admite_nulos() -> None:
    mock_db = AsyncMock()

    with patch("kallicode_core.auditoria.uno", new_callable=AsyncMock) as mock_uno:
        mock_uno.return_value = {"sello": "b" * 64}

        sello = await registrar_evento(
            mock_db,
            tenant_id="tenant_demo",
            evento="paso_llm",
            resumen="Ejecución de agente",
            actor_tipo="agente",
            actor_id="agent_design",
        )

        assert sello == "b" * 64
        mock_uno.assert_called_once()
        params = mock_uno.call_args[0][2]
        assert params["ip"] is None
        assert params["ua"] is None
        assert params["rid"] is None
