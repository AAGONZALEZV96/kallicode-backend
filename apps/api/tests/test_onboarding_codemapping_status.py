"""Pruebas del endpoint interno de estado de onboarding CodeMapping (KC-CM-10-2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main_interna import app


def _override_servicio(tenant_id: str):
    async def _dep() -> ServicioActual:
        return ServicioActual(servicio="orquestador-test", tenant_id=tenant_id, linea=1)

    return _dep


@asynccontextmanager
async def _sesion_falsa(_svc: ServicioActual) -> AsyncIterator[object]:
    yield object()


@pytest.mark.asyncio
async def test_onboarding_codemapping_status_en_progreso() -> None:
    """Devuelve fase activa (1..5), porcentaje, nodos/aristas y errores del tenant."""
    tenant = "org_tenant_alpha"
    app.dependency_overrides[servicio_actual] = _override_servicio(tenant)

    iniciado = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
    snap_mock = {
        "id": "snap_alpha_12345678",
        "commit_sha": "abcdef1234567890",
        "rama": "main",
        "estado": "en_progreso",
        "fase_actual": "fase_3",
        "stats": {
            "checkpoint_fase": 3,
            "events": [
                {
                    "fase": "fase_2",
                    "level": "WARN",
                    "mensaje": "Archivo binario omitido",
                    "timestamp": "2026-11-15T05:01:00+00:00",
                    "trace_id": "tr_1",
                },
                {
                    "fase": "fase_3",
                    "level": "ERROR",
                    "mensaje": "Timeout parcial resolviendo vista SQL",
                    "timestamp": "2026-11-15T05:02:00+00:00",
                    "trace_id": "tr_1",
                },
            ],
        },
        "iniciado_en": iniciado,
        "finalizado_en": None,
    }

    try:
        with (
            patch("kallicode_api.routers.internal.sesion_de", _sesion_falsa),
            patch(
                "kallicode_api.routers.internal.uno",
                new_callable=AsyncMock,
                side_effect=[snap_mock, {"total": 42}, {"total": 87}],
            ) as mock_uno,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/internal/v1/onboarding/codemapping/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["version_contrato"] == "1.0"
        assert body["tenant_id"] == tenant
        assert body["snapshot_id"] == "snap_alpha_12345678"
        assert body["estado"] == "en_progreso"
        assert body["fase_activa"] == 3
        assert body["fase_nombre"] == "resolucion_esquema_sql_orm"
        assert body["porcentaje_avance"] == 60
        assert body["conteo_nodos"] == 42
        assert body["conteo_aristas"] == 87
        assert len(body["errores"]) == 1
        assert body["errores"][0]["fase"] == "fase_3"

        # Verifica aislamiento estricto por tenant_id en las 3 consultas SQL
        for llamada in mock_uno.call_args_list:
            params = llamada.args[2]
            assert params["t"] == tenant
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_onboarding_codemapping_status_sin_iniciar_y_aislamiento() -> None:
    """Si el tenant B no tiene snapshots, no filtra ni expone datos de otro tenant."""
    tenant_b = "org_tenant_beta"
    app.dependency_overrides[servicio_actual] = _override_servicio(tenant_b)

    try:
        with (
            patch("kallicode_api.routers.internal.sesion_de", _sesion_falsa),
            patch(
                "kallicode_api.routers.internal.uno",
                new_callable=AsyncMock,
                side_effect=[None, {"total": 0}, {"total": 0}],
            ) as mock_uno,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/internal/v1/onboarding/codemapping/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == tenant_b
        assert body["estado"] == "sin_iniciar"
        assert body["fase_activa"] == 0
        assert body["porcentaje_avance"] == 0
        assert body["conteo_nodos"] == 0
        assert body["conteo_aristas"] == 0
        assert body["errores"] == []
        assert all(call.args[2]["t"] == tenant_b for call in mock_uno.call_args_list)
    finally:
        app.dependency_overrides.clear()
