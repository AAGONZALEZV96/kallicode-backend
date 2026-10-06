from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given, settings
from hypothesis import strategies as st
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
                    "estado": "pendiente_asignacion",
                    "iteraciones": {},
                }
                mock_trans.return_value = {
                    "job_id": "job_123",
                    "de_estado": "pendiente_asignacion",
                    "a_estado": "triage",
                    "iteracion": 0,
                    "iteraciones": {},
                }

                async def _mock_uno(*args, **kwargs):
                    query = args[1] if len(args) > 1 else ""
                    if "FOR UPDATE" in query:
                        return {
                            "id": "job_123",
                            "estado": "pendiente_asignacion",
                            "ticket_id": "tk_1",
                        }
                    return {"numero": 101}

                mock_uno.side_effect = _mock_uno

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
                assert data["de"] == "pendiente_asignacion"
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
                        "estado": "pendiente_asignacion",
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


ESTADOS_POSIBLES = [
    "pendiente_asignacion",
    "triage",
    "design",
    "gate1",
    "build",
    "qa",
    "gate2",
    "security",
    "deploy_preflight",
    "gate3",
    "deploy",
    "produccion",
    "pausado_por_cuota",
    "escalado_humano",
    "cancelado",
    "cerrado_duplicado",
]

TRANSICIONES_VALIDAS = {
    "pendiente_asignacion": {"triage", "cancelado", "pausado_por_cuota"},
    "triage": {"design", "cerrado_duplicado", "cancelado", "escalado_humano"},
    "design": {"gate1", "cancelado", "escalado_humano"},
    "gate1": {"build", "design", "cancelado"},
    "build": {"qa", "design", "cancelado", "escalado_humano", "pausado_por_cuota"},
    "qa": {"gate2", "build", "cancelado", "escalado_humano"},
    "gate2": {"security", "build", "cancelado"},
    "security": {"deploy_preflight", "build", "cancelado", "escalado_humano"},
    "deploy_preflight": {"gate3", "build", "cancelado"},
    "gate3": {"deploy", "design", "build", "qa", "cancelado"},
    "deploy": {"produccion", "escalado_humano", "cancelado"},
    "pausado_por_cuota": {"triage", "build", "cancelado"},
    "escalado_humano": {"triage", "design", "build", "qa", "security", "deploy", "cancelado"},
}


@pytest.mark.anyio
@settings(max_examples=50, deadline=None)
@given(origen=st.sampled_from(ESTADOS_POSIBLES), destino=st.sampled_from(ESTADOS_POSIBLES))
async def test_propiedad_grafo_estados_seguro(origen, destino):
    """
    Prueba basada en propiedades: Verifica que CUALQUIER par (origen, destino)
    no declarado en el grafo sea bloqueado con un 409 Conflict.
    """
    if destino in TRANSICIONES_VALIDAS.get(origen, set()):
        return  # Es una transición válida, la omitimos en esta prueba negativa

    # Setup rápido del mock
    mock_svc = ServicioActual(servicio="orquestador", tenant_id="tenant_test", linea="estandar")
    app.dependency_overrides[servicio_actual] = lambda: mock_svc

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("kallicode_api.routers.internal.uno", new_callable=AsyncMock) as mock_uno,
            patch("kallicode_api.routers.internal.con_idempotencia") as mock_idem,
        ):
            # Bypass de idempotencia
            async def _bypass_idem(**kwargs):
                return await kwargs["logica"]()

            mock_idem.side_effect = _bypass_idem

            # Simulamos el SELECT ... FOR UPDATE (Debe devolver el job en el estado 'origen')
            async def _mock_uno(*args, **kwargs):
                query = args[1] if len(args) > 1 else ""
                if "FOR UPDATE" in query:
                    return {"id": "job_prop", "estado": origen, "ticket_id": "tk_1"}
                return {"numero": 101}

            mock_uno.side_effect = _mock_uno

            payload = {"a": destino, "motivo": "Property Test", "actor": "svc:orquestador"}
            headers = {
                "X-Tenant-Id": "tenant_test",
                "Idempotency-Key": f"key-prop-{origen}-{destino}",
            }

            res = await client.patch(
                "/internal/v1/jobs/job_prop/state", json=payload, headers=headers
            )

            # La aserción matemática de la propiedad: TODA transición ilegal DEBE ser un 409
            assert res.status_code == 409, (
                f"Falla Crítica: Se permitió transición ilegal de '{origen}' a '{destino}'"
            )
