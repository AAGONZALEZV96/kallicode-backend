from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_api.routers.internal import ServicioActual, servicio_actual


@pytest.mark.anyio
async def test_transicion_exitosa_via_api():
    # 1. Mock de la dependencia de autenticación del servicio
    mock_svc = ServicioActual(servicio="orquestador", tenant_id="tenant_test", linea="estandar")
    app.dependency_overrides[servicio_actual] = lambda: mock_svc

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with (
                patch("kallicode_api.routers.internal.sesion_de") as mock_sesion,
                patch(
                    "kallicode_api.routers.internal._job_activo", new_callable=AsyncMock
                ) as mock_job,
                patch(
                    "kallicode_api.routers.internal.transicionar",
                    new_callable=AsyncMock,
                ) as mock_trans,
                patch("kallicode_api.routers.internal.uno", new_callable=AsyncMock) as mock_uno,
                patch(
                    "kallicode_api.routers.internal.registrar_evento",
                    new_callable=AsyncMock,
                ) as _,
                patch("kallicode_api.routers.internal.con_idempotencia") as mock_idem,
            ):
                # Configurar context manager asíncrono para sesion_de
                mock_db = AsyncMock()
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_db
                mock_ctx.__aexit__.return_value = None
                mock_sesion.return_value = mock_ctx

                # Bypass de idempotencia ejecutando la lógica interna directamente
                async def _bypass_idem(**kwargs):
                    return await kwargs["logica"]()

                mock_idem.side_effect = _bypass_idem

                mock_job.return_value = {
                    "id": "job_123",
                    "ticket_id": "tk_1",
                    "estado": "creado",
                    "iteraciones": {},
                }
                mock_trans.return_value = {
                    "job_id": "job_123",
                    "de_estado": "creado",
                    "a_estado": "triage",
                    "iteracion": 0,
                    "iteraciones": {},
                }
                mock_uno.return_value = {"numero": 101}

                headers = {
                    "X-Tenant-Id": "tenant_test",
                    "Idempotency-Key": "key-test-123",
                }
                payload = {
                    "a": "triage",
                    "motivo": "inicio automatico",
                    "actor": "svc:orquestador",
                }

                res = await client.patch(
                    "/internal/v1/jobs/job_123/state", json=payload, headers=headers
                )

                assert res.status_code == 200
                data = res.json()
                assert data["de"] == "creado"
                assert data["a"] == "triage"
                assert mock_trans.await_count == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_listar_jobs_paginado():
    mock_svc = ServicioActual(servicio="orquestador", tenant_id="tenant_test", linea="estandar")
    app.dependency_overrides[servicio_actual] = lambda: mock_svc

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with (
                patch("kallicode_api.routers.internal.sesion_de") as mock_sesion,
                patch("kallicode_api.routers.internal.uno", new_callable=AsyncMock) as mock_uno,
                patch("kallicode_api.routers.internal.todos", new_callable=AsyncMock) as mock_todos,
            ):
                mock_db = AsyncMock()
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_db
                mock_ctx.__aexit__.return_value = None
                mock_sesion.return_value = mock_ctx

                mock_uno.return_value = {"total": 2}
                mock_todos.return_value = [
                    {
                        "id": "job_1",
                        "tenant_id": "tenant_test",
                        "ticket_numero": "TK-100",
                        "estado": "creado",
                        "payload": "{}",
                        "iteraciones": {},
                        "actualizado_en": "2026-09-25T12:00:00Z",
                    },
                    {
                        "id": "job_2",
                        "tenant_id": "tenant_test",
                        "ticket_numero": "TK-101",
                        "estado": "triage",
                        "payload": "{}",
                        "iteraciones": {},
                        "actualizado_en": "2026-09-25T12:30:00Z",
                    },
                ]

                res = await client.get("/internal/v1/jobs?page=1&page_size=10")

                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 2
                assert data["page"] == 1
                assert data["page_size"] == 10
                assert len(data["items"]) == 2
                assert data["items"][0]["id"] == "job_1"
                assert data["items"][1]["id"] == "job_2"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_listar_jobs_con_filtros():
    mock_svc = ServicioActual(servicio="orquestador", tenant_id="tenant_test", linea="estandar")
    app.dependency_overrides[servicio_actual] = lambda: mock_svc

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with (
                patch("kallicode_api.routers.internal.sesion_de") as mock_sesion,
                patch("kallicode_api.routers.internal.uno", new_callable=AsyncMock) as mock_uno,
                patch("kallicode_api.routers.internal.todos", new_callable=AsyncMock) as mock_todos,
            ):
                mock_db = AsyncMock()
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_db
                mock_ctx.__aexit__.return_value = None
                mock_sesion.return_value = mock_ctx

                mock_uno.return_value = {"total": 1}
                mock_todos.return_value = [
                    {
                        "id": "job_2",
                        "tenant_id": "tenant_test",
                        "ticket_numero": "TK-101",
                        "estado": "triage",
                        "payload": "{}",
                        "iteraciones": {},
                        "actualizado_en": "2026-09-25T12:30:00Z",
                    }
                ]

                res = await client.get("/internal/v1/jobs?ticket_id=TK-101&estado=triage")

                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 1
                assert len(data["items"]) == 1
                assert data["items"][0]["ticket_numero"] == "TK-101"
                assert data["items"][0]["estado"] == "triage"
    finally:
        app.dependency_overrides.clear()
