"""Equipo desde el portal (KC-PT-10-5): invitar y cambiar el rol o el estado de un usuario.

Los errores que el portal muestra tal cual. Antes de registrar INVITACION_DUPLICADA,
ROL_NO_AUTORIZADO, ULTIMO_OWNER y ULTIMO_ARQUITECTO en CATALOGO_ERRORES, AppError lanzaba
ValueError y el backend respondía 500.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


@pytest_asyncio.fixture
async def org():
    """Owner, admin y un arquitecto, con el plan growth (25 usuarios)."""
    t = f"org_eq_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Equipo e2e')"), {"t": t}
        )
        await db.execute(
            text(
                """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
                   VALUES (:t, :t, 'growth', current_date - 5, current_date + 25)"""
            ),
            {"t": t},
        )
        for rol in ("owner", "admin", "architect"):
            await db.execute(
                text(
                    """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                       VALUES (:t || '_' || :r, :t, :t || '.' || :r || '@example.com', :r, :r)"""
                ),
                {"t": t, "r": rol},
            )

    def como(rol: str) -> dict:
        token = emitir_access_token(f"{t}_{rol}", t, rol)
        return {"Authorization": f"Bearer {token}"}

    yield {"tenant": t, "como": como}
    async with sesion_sistema() as db:
        for tabla in (
            "core.invitations",
            "core.refresh_tokens",
            "core.users",
            "core.subscriptions",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": t})


def _codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["codigo"]


async def test_invitar_y_la_segunda_invitacion_al_mismo_correo_responde_409(client, org) -> None:
    cuerpo = {"email": "Nueva@Example.com", "rol": "member"}
    r = await client.post("/api/v1/users/invitations", headers=org["como"]("admin"), json=cuerpo)
    assert r.status_code == 201, r.text
    assert (r.json()["email"], r.json()["rol"], r.json()["estado"]) == (
        "nueva@example.com",
        "member",
        "enviada",
    )

    otra = await client.post("/api/v1/users/invitations", headers=org["como"]("owner"), json=cuerpo)
    assert _codigo(otra) == (409, "INVITACION_DUPLICADA")


async def test_un_admin_no_puede_invitar_administradores(client, org) -> None:
    r = await client.post(
        "/api/v1/users/invitations",
        headers=org["como"]("admin"),
        json={"email": "jefa@example.com", "rol": "admin"},
    )

    assert _codigo(r) == (403, "ROL_NO_AUTORIZADO")


async def test_el_ultimo_owner_no_puede_dejar_de_serlo(client, org) -> None:
    t = org["tenant"]
    r = await client.patch(
        f"/api/v1/users/{t}_owner", headers=org["como"]("owner"), json={"rol": "admin"}
    )

    assert _codigo(r) == (409, "ULTIMO_OWNER")


async def test_el_unico_arquitecto_no_puede_perder_el_rol_ni_desactivarse(client, org) -> None:
    t = org["tenant"]
    for cambio in ({"rol": "member"}, {"estado": "desactivado"}):
        r = await client.patch(
            f"/api/v1/users/{t}_architect", headers=org["como"]("owner"), json=cambio
        )
        assert _codigo(r) == (409, "ULTIMO_ARQUITECTO"), cambio


async def test_cambiar_rol_y_desactivar_un_usuario(client, org) -> None:
    t = org["tenant"]
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                   VALUES (:i, :t, :i || '@example.com', 'Miembro', 'member')"""
            ),
            {"i": f"{t}_member", "t": t},
        )

    rol = await client.patch(
        f"/api/v1/users/{t}_member", headers=org["como"]("admin"), json={"rol": "approver"}
    )
    assert rol.status_code == 200, rol.text
    assert rol.json()["rol"] == "approver"
    baja = await client.patch(
        f"/api/v1/users/{t}_member", headers=org["como"]("admin"), json={"estado": "desactivado"}
    )
    assert baja.status_code == 200, baja.text
    assert baja.json()["estado"] == "desactivado"
