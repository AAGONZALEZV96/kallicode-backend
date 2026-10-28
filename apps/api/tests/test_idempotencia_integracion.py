import pytest
from fastapi import Depends, Header
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main import app
from kallicode_api.routers.internal import endpoint_idempotente
from kallicode_core.db import sesion_sistema
from kallicode_core.ids import nuevo
from pydantic import BaseModel
from sqlalchemy import text


# 1. Esquema simulado (como JobIn)
class PayloadSimulado(BaseModel):
    ticket_numero: str


# 2. Endpoint aislado exclusivo para probar el decorador atómico
@app.post("/internal/v1/test-idempotencia", include_in_schema=False)
@endpoint_idempotente("endpoint_aislado", 201)
async def endpoint_prueba(
    datos: PayloadSimulado,
    svc: ServicioActual = Depends(servicio_actual),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    return {"exito": True, "recurso_procesado": datos.ticket_numero}


@pytest.mark.asyncio
async def test_doble_llamada_idempotente():
    # Setup de base de datos
    t_id = nuevo("t")
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant Test') ON CONFLICT DO NOTHING"
            ),
            {"id": t_id},
        )
        await db.commit()

    # Mock de autenticación
    mock_svc = ServicioActual(servicio="orquestador", tenant_id=t_id, linea=1)
    app.dependency_overrides[servicio_actual] = lambda: mock_svc

    payload = {"ticket_numero": "TK-TEST-99"}
    headers = {"Idempotency-Key": "llave_concurrencia_777"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # LLAMADA 1: Entra, ejecuta la función simulada y guarda en core.idempotency_keys
        res1 = await ac.post("/internal/v1/test-idempotencia", json=payload, headers=headers)
        assert res1.status_code == 201, f"Fallo llamada 1: {res1.text}"

        # LLAMADA 2 (El Reintento): El decorador lo ataja en PostgreSQL y devuelve la caché
        res2 = await ac.post("/internal/v1/test-idempotencia", json=payload, headers=headers)

        # Afirmaciones críticas
        assert res2.status_code == 201, f"El decorador no devolvió 201: {res2.text}"
        assert res1.json() == res2.json(), "El cuerpo recuperado de la caché no es idéntico"

    app.dependency_overrides.clear()
