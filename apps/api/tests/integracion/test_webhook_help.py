"""Tests de KC-BE-04-6 (cierra BE-F23).

- Webhook Help bien firmado a un tenant Starter (permite_help=false) →
  403 FUNCION_NO_DISPONIBLE, CERO filas nuevas en core.webhook_inbox,
  CERO mensajes nuevos en kc:inbox.
- Webhook Help con firma inválida → 401 con cuerpo mínimo que NO revela
  el tenant, el plan ni la existencia de la conexión.

Requiere PostgreSQL real. Se marca como integración.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

import httpx
import pytest
from httpx import ASGITransport
from kallicode_api.main import app
from kallicode_api.servicios import boveda
from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, uno
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


# --------------------------------------------------------------- helpers
async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {"id": tenant_id, "n": f"Tenant {tenant_id}"},
        )
    return tenant_id


async def _borrar_tenant(tenant_id: str) -> None:
    async with sesion_sistema() as db:
        try:
            await db.rollback()
        except Exception:
            pass
        for tabla in (
            "core.usage_cycles",
            "core.subscriptions",
            "core.notifications",
            "core.webhook_inbox",
            "core.tickets",
            "core.connections",
        ):
            try:
                await db.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            except Exception:
                pass
        try:
            await db.execute(
                text("DELETE FROM core.organizations WHERE id = :t"),
                {"t": tenant_id},
            )
        except Exception:
            pass


async def _crear_suscripcion(tenant_id: str, plan_codigo: str) -> None:
    """Crea una suscripción activa al plan indicado."""
    sub_id = f"sub_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        plan = await uno(db, "SELECT codigo FROM core.plans WHERE codigo = :c", {"c": plan_codigo})
        if plan is None:
            pytest.skip(f"no existe core.plans.codigo={plan_codigo}")
        await db.execute(
            text("""
                INSERT INTO core.subscriptions (
                    id, tenant_id, plan_codigo, estado,
                    inicia_el, renueva_el, finaliza_el
                )
                VALUES (
                    :id, :t, :p, 'activa',
                    current_date, current_date + interval '1 month', NULL
                )
            """),
            {"id": sub_id, "t": tenant_id, "p": plan_codigo},
        )


async def _crear_conexion(tenant_id: str, secreto: str) -> tuple[str, str]:
    """Crea una conexión con ruta_token única y su secreto en la bóveda.

    Devuelve (connection_id, ruta_token).
    """
    connection_id = f"conn_{secrets.token_hex(6)}"
    ruta_token = f"wh_{secrets.token_hex(8)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.connections (
                    id, tenant_id, categoria, proveedor, nombre, estado,
                    ruta_token, config
                )
                VALUES (:id, :t, 'tickets', 'kallicode_help', 'Test Help', 'conectada',
                        :rt, CAST(:cfg AS jsonb))
            """),
            {
                "id": connection_id,
                "t": tenant_id,
                "rt": ruta_token,
                "cfg": json.dumps({}),
            },
        )
    await boveda.guardar(tenant_id, f"cn--{connection_id}", {"webhook_secret": secreto})
    return connection_id, ruta_token


def _firmar(cuerpo: bytes, secreto: str) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


async def _contar_inbox_y_stream(tenant_id: str) -> tuple[int, int]:
    async with sesion_sistema() as db:
        n = await uno(
            db,
            "SELECT count(*) AS n FROM core.webhook_inbox WHERE tenant_id = :t",
            {"t": tenant_id},
        )
    r = comercial.redis_cliente()
    largo = await r.xlen("kc:inbox")
    return int(n["n"]), int(largo)


# ------------------------------------------------------------------ tests
async def test_help_con_plan_starter_403_sin_crear_nada() -> None:
    """Webhook Help bien firmado a un tenant Starter → 403, 0 filas, 0 mensajes."""
    tenant = await _crear_tenant()
    try:
        await _crear_suscripcion(tenant, "starter")
        _conn_id, ruta_token = await _crear_conexion(tenant, "secreto_starter_test")
        cuerpo = json.dumps(
            {
                "tipo": "bug",
                "titulo": "Algo se rompe",
                "descripcion": "Detalle",
            }
        ).encode()
        firma = _firmar(cuerpo, "secreto_starter_test")

        antes_inbox, antes_stream = await _contar_inbox_y_stream(tenant)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/webhooks/kallicode-help/{ruta_token}",
                content=cuerpo,
                headers={
                    "X-KC-Signature": firma,
                    "X-KC-Delivery": f"deliv_{secrets.token_hex(6)}",
                },
            )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["codigo"] == "FUNCION_NO_DISPONIBLE"

        despues_inbox, despues_stream = await _contar_inbox_y_stream(tenant)
        assert despues_inbox == antes_inbox, (
            f"se creó una fila en core.webhook_inbox: {antes_inbox} → {despues_inbox}"
        )
        assert despues_stream == antes_stream, (
            f"se publicó un mensaje en kc:inbox: {antes_stream} → {despues_stream}"
        )
    finally:
        await _borrar_tenant(tenant)


async def test_help_con_firma_invalida_401_sin_revelar() -> None:
    """Firma inválida → 401 con cuerpo mínimo, 0 filas, 0 mensajes."""
    tenant = await _crear_tenant()
    try:
        await _crear_suscripcion(tenant, "starter")
        _conn_id, ruta_token = await _crear_conexion(tenant, "secreto_real")
        cuerpo = json.dumps(
            {
                "tipo": "bug",
                "titulo": "Algo se rompe",
                "descripcion": "Detalle",
            }
        ).encode()
        # Firma calculada con OTRO secreto
        firma_ajena = _firmar(cuerpo, "secreto_distinto")

        antes_inbox, antes_stream = await _contar_inbox_y_stream(tenant)

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/webhooks/kallicode-help/{ruta_token}",
                content=cuerpo,
                headers={
                    "X-KC-Signature": firma_ajena,
                    "X-KC-Delivery": f"deliv_{secrets.token_hex(6)}",
                },
            )

        assert resp.status_code == 401, resp.text
        # Cuerpo mínimo: no revela tenant, plan, ni conexión
        cuerpo_resp = resp.text.lower()
        for palabra in (
            "starter",
            "growth",
            "enterprise",
            tenant.lower(),
            "connection",
            "conexion",
        ):
            assert palabra not in cuerpo_resp, f"la respuesta 401 filtra '{palabra}': {resp.text}"

        despues_inbox, despues_stream = await _contar_inbox_y_stream(tenant)
        assert despues_inbox == antes_inbox
        assert despues_stream == antes_stream
    finally:
        await _borrar_tenant(tenant)
