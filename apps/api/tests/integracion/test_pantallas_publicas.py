"""Restablecer contraseña y aceptar invitación con sus errores (KC-PT-10-6).

Las pantallas públicas del portal muestran estos errores tal cual. Antes de registrar
AUTH_TOKEN_RESET_INVALIDO, INVITACION_INVALIDA y USUARIO_YA_EXISTE en CATALOGO_ERRORES, AppError
lanzaba ValueError y el backend respondía 500 en vez del error con su mensaje.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import generar_token_opaco
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

# Fuerte también para zxcvbn (que penaliza «kallicode»).
CLAVE = "Tq8vLm2Rx9pWz4nB"


@pytest_asyncio.fixture
async def org():
    t = f"org_pub_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        for sql in (
            "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Pantallas públicas')",
            """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
               VALUES (:t, :t, 'starter', current_date - 5, current_date + 25)""",
            """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
               VALUES (:t, :t, :t || '@example.test', 'Owner', 'owner')""",
        ):
            await db.execute(text(sql), {"t": t})
    yield t
    async with sesion_sistema() as db:
        for tabla in (
            "core.password_reset_tokens",
            "core.refresh_tokens",
            "core.invitations",
            "core.users",
            "core.subscriptions",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": t})


async def _token_reset(t: str) -> str:
    claro, h = generar_token_opaco("kcpr")
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.password_reset_tokens (id, tenant_id, user_id, token_hash,
                       expira_en)
                   VALUES (:i, :t, :t, :h, now() + interval '30 minutes')"""
            ),
            {"i": f"pr_{uuid.uuid4().hex}", "t": t, "h": h},
        )
    return claro


async def _invitacion(t: str, email: str) -> str:
    claro, h = generar_token_opaco("kcin")
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.invitations (id, tenant_id, email, rol, token_hash,
                       invitado_por, expira_en)
                   VALUES (:i, :t, :e, 'member', :h, :t, now() + interval '7 days')"""
            ),
            {"i": f"inv_{uuid.uuid4().hex}", "t": t, "e": email, "h": h},
        )
    return claro


def _error(r) -> tuple[int, str, str]:
    e = r.json()["error"]
    return r.status_code, e["codigo"], e["mensaje"]


async def test_un_enlace_de_restablecimiento_desconocido_responde_400(client, org) -> None:
    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "kcpr_no_existe", "password_nueva": CLAVE},
    )

    assert _error(r) == (
        400,
        "AUTH_TOKEN_RESET_INVALIDO",
        "El enlace ha expirado o ya fue usado. Solicita uno nuevo.",
    )


async def test_restablecer_una_vez_y_el_enlace_queda_usado(client, org) -> None:
    token = await _token_reset(org)

    debil = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password_nueva": "solominusculas"},
    )
    assert debil.status_code == 422
    assert debil.json()["error"]["codigo"] == "AUTH_PASSWORD_DEBIL"
    assert "al menos una mayúscula" in debil.json()["error"]["detalle"]["requisitos"]

    ok = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password_nueva": CLAVE}
    )
    assert ok.status_code == 200, ok.text
    otra = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "password_nueva": CLAVE}
    )
    assert _error(otra)[:2] == (400, "AUTH_TOKEN_RESET_INVALIDO")


async def test_una_invitacion_desconocida_responde_400(client, org) -> None:
    r = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": "kcin_no_existe", "nombre": "Ana", "password": CLAVE},
    )

    assert _error(r)[:2] == (400, "INVITACION_INVALIDA")


async def test_aceptar_entra_y_la_invitacion_no_se_usa_dos_veces(client, org) -> None:
    token = await _invitacion(org, f"nueva@{org}.test")

    r = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "nombre": "Ana Nueva", "password": CLAVE, "idioma": "en"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["refresh_token"]
    assert r.json()["usuario"]["rol"] == "member"

    otra = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "nombre": "Ana Nueva", "password": CLAVE},
    )
    assert _error(otra)[:2] == (400, "INVITACION_INVALIDA")


async def test_invitar_un_correo_que_ya_tiene_cuenta_responde_409(client, org) -> None:
    token = await _invitacion(org, f"{org}@example.test")

    r = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "nombre": "Owner otra vez", "password": CLAVE},
    )

    assert _error(r)[:2] == (409, "USUARIO_YA_EXISTE")
