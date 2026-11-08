import pytest
from fastapi import Depends, Header
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main_interna import crear_app_interna
from kallicode_api.routers.internal import endpoint_idempotente
from kallicode_core.db import sesion_sistema
from kallicode_core.ids import nuevo
from pydantic import BaseModel
from sqlalchemy import text

# Instancia exclusiva para no agregar rutas al singleton público.
app = crear_app_interna()


class PayloadSimulado(BaseModel):
    ticket_numero: str


@app.post(
    "/internal/v1/test-idempotencia",
    include_in_schema=False,
)
@endpoint_idempotente("endpoint_aislado", 201)
async def endpoint_prueba(
    datos: PayloadSimulado,
    svc: ServicioActual = Depends(servicio_actual),
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    return {
        "exito": True,
        "recurso_procesado": datos.ticket_numero,
    }


@pytest.mark.asyncio
async def test_doble_llamada_idempotente():
    tenant_id = nuevo("t")

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                INSERT INTO core.organizations (id, nombre)
                VALUES (:id, 'Tenant Test')
                ON CONFLICT DO NOTHING
                """
            ),
            {"id": tenant_id},
        )
        await db.commit()

    servicio = ServicioActual(
        servicio="orquestador",
        tenant_id=tenant_id,
        linea=1,
    )
    app.dependency_overrides[servicio_actual] = lambda: servicio

    payload = {"ticket_numero": "TK-TEST-99"}
    headers = {
        "Idempotency-Key": "llave_concurrencia_777",
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            primera = await client.post(
                "/internal/v1/test-idempotencia",
                json=payload,
                headers=headers,
            )
            assert primera.status_code == 201, primera.text

            segunda = await client.post(
                "/internal/v1/test-idempotencia",
                json=payload,
                headers=headers,
            )
            assert segunda.status_code == 201, segunda.text
            assert primera.json() == segunda.json()
    finally:
        app.dependency_overrides.clear()
