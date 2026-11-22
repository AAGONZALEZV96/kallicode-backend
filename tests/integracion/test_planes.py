"""KC-BE-09-4: comparador, mejora comercial y acceso suspendido."""

from contextlib import asynccontextmanager
from secrets import token_urlsafe
from unittest.mock import Mock
from uuid import uuid4

import pytest
from kallicode_api.routers import auth, organization, plans, usage
from kallicode_core.comercial import ciclo_de
from kallicode_core.config import get_settings
from kallicode_core.db import todos
from kallicode_core.seguridad import emitir_access_token, emitir_svc_token, hashear_password
from kallicode_workers import notifier
from sqlalchemy import text

pytestmark = pytest.mark.integracion
_SOLICITUD = "/api/v1/plans/upgrade-request"


@pytest.fixture
async def organizacion(db_admin, monkeypatch, redis_doble):
    db = db_admin
    tenant = f"org_planes_{uuid4().hex}"
    email = f"{tenant}@example.com"
    password = token_urlsafe(24)
    password_hash = hashear_password(password)
    for sql in (
        "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Prueba de planes')",
        """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
           VALUES (:t, :t, 'starter', current_date - 15, current_date + 15)""",
        """INSERT INTO core.users (id, tenant_id, email, nombre, rol, password_hash)
           VALUES (:t, :t, :email, 'Propietario', 'owner', :password)""",
        "INSERT INTO core.production_lines (tenant_id, numero) VALUES (:t, 1)",
    ):
        await db.execute(text(sql), {"t": tenant, "email": email, "password": password_hash})

    @asynccontextmanager
    async def sesion(*args, **kwargs):
        async with db.begin_nested():
            yield db

    for router in (plans, organization, usage):
        monkeypatch.setattr(router, "sesion_de", sesion)
    monkeypatch.setattr(auth, "sesion_sistema", sesion)
    monkeypatch.setattr(auth, "sesion_tenant", sesion)
    monkeypatch.setattr(notifier, "sesion_sistema", sesion)
    monkeypatch.setattr(notifier, "sesion_tenant", sesion)
    return db, tenant, email, password


def _usuario(tenant):
    return {"Authorization": f"Bearer {emitir_access_token(tenant, tenant, 'owner')}"}


async def test_comparador_marca_el_vigente_y_muestra_deltas(organizacion, client):
    _, tenant, _, _ = organizacion
    respuesta = await client.get("/api/v1/plans", headers=_usuario(tenant))
    assert respuesta.status_code == 200, respuesta.text
    catalogo = {p["codigo"]: p for p in respuesta.json()["planes"]}
    assert [p["codigo"] for p in catalogo.values() if p["actual"]] == ["starter"]
    assert catalogo["growth"]["delta"] == {
        "loc_mes": 5000,
        "lineas": 2,
        "max_usuarios": 20,
        "storage_mb": 20480,
    }
    assert set(catalogo["starter"]["delta"].values()) == {0}
    assert set(catalogo["enterprise"]["delta"].values()) == {None}


async def test_solicitud_notifica_y_aprobacion_idempotente(
    organizacion,
    client,
    client_interna,
    monkeypatch,
):
    db, tenant, _, _ = organizacion
    ciclo = await ciclo_de(db, tenant)
    await db.execute(
        text("""
            INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste)
            VALUES (:t, :c, 'ajuste', 3000, 'Consumo de prueba')
        """),
        {"t": tenant, "c": ciclo},
    )
    usuario = _usuario(tenant)
    antes = await client.get("/api/v1/usage/summary", headers=usuario)
    assert antes.json()["umbral"] == "agotado"
    solicitud = await client.post(_SOLICITUD, headers=usuario, json={"plan_deseado": "growth"})
    assert solicitud.status_code == 202, solicitud.text
    solicitud_id = solicitud.json()["solicitud_id"]
    estado = await client.get(_SOLICITUD, headers=usuario)
    assert estado.json()["estado"] == "registrada"
    assert estado.json()["urgente"] is True

    monkeypatch.setattr(get_settings(), "comercial_email", "comercial@example.com")
    envio = Mock()
    monkeypatch.setattr(notifier, "_enviar_smtp", envio)
    await notifier.avisar_comercial()
    await notifier.avisar_comercial()
    (aviso,) = [llamada.args for llamada in envio.call_args_list if solicitud_id in llamada.args[2]]
    destino, asunto, cuerpo = aviso
    assert (destino, asunto) == ("comercial@example.com", "Solicitud urgente de mejora")
    assert solicitud_id in cuerpo
    repetida = await client.post(_SOLICITUD, headers=usuario, json={"plan_deseado": "growth"})
    assert repetida.json()["solicitud_id"] == solicitud_id
    assert repetida.json()["estado"] == "en_gestion"

    ruta = f"/internal/v1/plans/upgrade-requests/{solicitud_id}/approve"
    for servicio, org, esperado in (
        ("orquestador", tenant, 403),
        ("comercial", "org_ajena", 404),
        ("comercial", tenant, 200),
        ("comercial", tenant, 200),
    ):
        respuesta = await client_interna.post(
            ruta,
            headers={
                "Authorization": f"Bearer {emitir_svc_token(servicio, org)}",
                "Idempotency-Key": uuid4().hex,
            },
        )
        assert respuesta.status_code == esperado, respuesta.text
        if esperado == 200:
            assert respuesta.json()["estado"] == "aplicada"

    despues = await client.get("/api/v1/usage/summary", headers=usuario)
    assert despues.json()["ciclo"] == ciclo
    assert despues.json()["plan"]["loc_mes"] == 8000
    assert despues.json()["loc"]["consumidas"] == 3000
    assert despues.json()["loc"]["restantes"] == 5000
    assert despues.json()["umbral"] == "normal"
    org = await client.get("/api/v1/organization", headers=usuario)
    assert org.json()["plan"]["lineas"] == 3
    assert await todos(
        db,
        "SELECT numero FROM core.production_lines WHERE tenant_id = :t ORDER BY numero",
        {"t": tenant},
    ) == [{"numero": n} for n in (1, 2, 3)]
    assert await todos(
        db,
        """SELECT evento FROM audit.audit_events
            WHERE tenant_id = :t AND evento LIKE 'mejora_plan_%' ORDER BY id""",
        {"t": tenant},
    ) == [{"evento": "mejora_plan_solicitada"}, {"evento": "mejora_plan_aplicada"}]


async def test_suscripcion_suspendida_limita_el_acceso(organizacion, client):
    db, tenant, email, password = organizacion
    await db.execute(
        text("UPDATE core.subscriptions SET estado = 'suspendida' WHERE tenant_id = :t"),
        {"t": tenant},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    assert login.json()["acceso_restringido"] is True
    token = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ruta in (
        "/usage/summary",
        "/usage/by-ticket",
        "/plans",
        _SOLICITUD.removeprefix("/api/v1"),
        "/organization",
    ):
        respuesta = await client.get("/api/v1" + ruta, headers=token)
        assert respuesta.status_code == 200, (ruta, respuesta.text)
    for ruta in (
        "/dashboard/summary",
        "/tickets",
        "/users/me",
        "/connections",
        "/audit/events",
        "/notifications",
    ):
        respuesta = await client.get("/api/v1" + ruta, headers=token)
        assert respuesta.status_code == 403, (ruta, respuesta.text)
        assert respuesta.json()["error"]["codigo"] == "PERMISO_DENEGADO"
    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["acceso_restringido"] is True
