"""Perfil propio y ajustes de la organización desde el portal (KC-PT-10-4).

- PATCH /users/me con la contraseña actual equivocada responde PASSWORD_ACTUAL_INCORRECTA (400);
  sin registrarla en CATALOGO_ERRORES, AppError lanzaba ValueError y respondía 500.
- PATCH /organization con un If-Match viejo responde PRECONDICION_FALLIDA (412), y el CORS deja
  al portal (en otro origen) mandar If-Match y leer el ETag.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_access_token, hashear_password
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

ACTUAL = "Tq8vLm2Rx9pWz4nB"
NUEVA = "Hz7kPq3Wv8Ns2Lc5"


@pytest_asyncio.fixture
async def owner():
    t = f"org_perfil_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Perfil e2e')"), {"t": t}
        )
        await db.execute(
            text(
                """INSERT INTO core.users (id, tenant_id, email, nombre, rol, password_hash)
                   VALUES (:t, :t, :t || '@example.com', 'Owner', 'owner', :p)"""
            ),
            {"t": t, "p": hashear_password(ACTUAL)},
        )
    yield {
        "tenant": t,
        "headers": {"Authorization": f"Bearer {emitir_access_token(t, t, 'owner')}"},
    }
    async with sesion_sistema() as db:
        for tabla in ("core.refresh_tokens", "core.users"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": t})


async def test_la_contrasena_actual_equivocada_responde_400(client, owner) -> None:
    r = await client.patch(
        "/api/v1/users/me",
        headers=owner["headers"],
        json={"password_actual": "otra-cosa", "password_nueva": NUEVA},
    )

    assert r.status_code == 400, r.text
    assert r.json()["error"]["codigo"] == "PASSWORD_ACTUAL_INCORRECTA"


async def test_cambiar_nombre_idioma_y_contrasena(client, owner) -> None:
    r = await client.patch(
        "/api/v1/users/me",
        headers=owner["headers"],
        json={
            "nombre": "Olga Nueva",
            "idioma": "pt",
            "password_actual": ACTUAL,
            "password_nueva": NUEVA,
        },
    )

    assert r.status_code == 200, r.text
    assert (r.json()["nombre"], r.json()["idioma"]) == ("Olga Nueva", "pt")
    login = await client.post(
        "/api/v1/auth/login", json={"email": f"{owner['tenant']}@example.com", "password": NUEVA}
    )
    assert login.status_code == 200, login.text


async def test_un_if_match_viejo_responde_412_y_el_vigente_guarda(client, owner) -> None:
    h = owner["headers"]
    actual = await client.get("/api/v1/organization", headers=h)
    etag = actual.headers["etag"]

    ok = await client.patch(
        "/api/v1/organization",
        headers={**h, "If-Match": etag},
        json={"nombre": "Perfil e2e 2", "timezone": "America/Santiago"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["timezone"] == "America/Santiago"
    assert ok.headers["etag"] != etag

    viejo = await client.patch(
        "/api/v1/organization", headers={**h, "If-Match": etag}, json={"nombre": "Pisada"}
    )
    assert viejo.status_code == 412, viejo.text
    assert viejo.json()["error"]["codigo"] == "PRECONDICION_FALLIDA"


async def test_el_cors_permite_if_match_y_expone_el_etag(client, owner) -> None:
    previa = await client.options(
        "/api/v1/organization",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type,if-match",
        },
    )
    assert previa.status_code == 200, previa.text
    assert "if-match" in previa.headers["access-control-allow-headers"].lower()

    r = await client.get(
        "/api/v1/organization", headers={**owner["headers"], "Origin": "http://localhost:5173"}
    )
    assert "etag" in r.headers["access-control-expose-headers"].lower()
