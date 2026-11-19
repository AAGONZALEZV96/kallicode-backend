"""Tests del contrato transversal: sort en listados y ETag/If-Match (KC-BE-07-3)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


# --- Listados: sort ---


@pytest.mark.parametrize(
    "ruta,campo,direccion",
    [
        ("/api/v1/tickets", "actualizado_en", "-actualizado_en"),
        ("/api/v1/tickets", "prioridad", "prioridad"),
        ("/api/v1/tickets", "prioridad", "-prioridad"),
        ("/api/v1/users", "nombre", "nombre"),
        ("/api/v1/users", "nombre", "-nombre"),
        ("/api/v1/audit/events", "creado_en", "-creado_en"),
        ("/api/v1/audit/events", "creado_en", "creado_en"),
        ("/api/v1/usage/by-ticket", "actualizado_en", "-actualizado_en"),
        ("/api/v1/notifications", "creada_en", "-creada_en"),
        ("/api/v1/connections", "categoria", "categoria"),
        ("/api/v1/connections", "categoria", "-categoria"),
    ],
)
async def test_sort_acepta_campos_permitidos(
    client: AsyncClient,
    auth_headers_admin: dict,
    ruta: str,
    campo: str,
    direccion: str,
) -> None:
    r = await client.get(f"{ruta}?sort={direccion}", headers=auth_headers_admin)
    assert r.status_code == 200, r.text
    # El campo de orden se devuelve en la respuesta
    assert "sort" in r.json()


@pytest.mark.parametrize(
    "ruta",
    [
        "/api/v1/tickets",
        "/api/v1/users",
        "/api/v1/audit/events",
        "/api/v1/usage/by-ticket",
        "/api/v1/notifications",
        "/api/v1/connections",
    ],
)
async def test_sort_rechaza_campo_no_permitido(
    client: AsyncClient,
    auth_headers_admin: dict,
    ruta: str,
) -> None:
    r = await client.get(f"{ruta}?sort=password_hash", headers=auth_headers_admin)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["codigo"] == "VALIDACION_ENTRADA"


# --- PATCH: ETag e If-Match ---


async def test_patch_organization_devuelve_etag(
    client: AsyncClient,
    auth_headers_admin: dict,
    tenant_id: str,
) -> None:
    r = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org Nueva"},
        headers=auth_headers_admin,
    )
    assert r.status_code == 200, r.text
    assert "etag" in {k.lower() for k in r.headers}
    etag = r.headers["etag"]
    assert etag.startswith('W/"')


async def test_patch_organization_if_match_conflicto(
    client: AsyncClient,
    auth_headers_admin: dict,
) -> None:
    # PATCH 1: obtener ETag actual
    r1 = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org 1"},
        headers=auth_headers_admin,
    )
    assert r1.status_code == 200
    etag_viejo = r1.headers["etag"]

    # PATCH 2: usar el ETag viejo, pero otro PATCH ya cambió el recurso
    r2 = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org 2"},
        headers=auth_headers_admin,
    )
    assert r2.status_code == 200

    # PATCH 3: con el ETag viejo, debe fallar 412
    r3 = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org 3"},
        headers={**auth_headers_admin, "If-Match": etag_viejo},
    )
    assert r3.status_code == 412, r3.text
    assert r3.json()["error"]["codigo"] == "PRECONDICION_FALLIDA"


async def test_patch_organization_if_match_ok(
    client: AsyncClient,
    auth_headers_admin: dict,
) -> None:
    r1 = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org A"},
        headers=auth_headers_admin,
    )
    etag = r1.headers["etag"]
    r2 = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org B"},
        headers={**auth_headers_admin, "If-Match": etag},
    )
    assert r2.status_code == 200, r2.text


async def test_patch_users_if_match_conflicto(
    client: AsyncClient,
    auth_headers_admin: dict,
    user_id_destino: str,
) -> None:
    r1 = await client.patch(
        f"/api/v1/users/{user_id_destino}",
        json={"rol": "approver"},
        headers=auth_headers_admin,
    )
    etag_viejo = r1.headers["etag"]

    await client.patch(
        f"/api/v1/users/{user_id_destino}",
        json={"rol": "member"},
        headers=auth_headers_admin,
    )

    r3 = await client.patch(
        f"/api/v1/users/{user_id_destino}",
        json={"rol": "admin"},
        headers={**auth_headers_admin, "If-Match": etag_viejo},
    )
    assert r3.status_code == 412
    assert r3.json()["error"]["codigo"] == "PRECONDICION_FALLIDA"


async def test_patch_connections_if_match_conflicto(
    client: AsyncClient,
    auth_headers_admin: dict,
    connection_id: str,
) -> None:
    r1 = await client.patch(
        f"/api/v1/connections/{connection_id}",
        json={"nombre": "Conexión 1"},
        headers=auth_headers_admin,
    )
    etag_viejo = r1.headers["etag"]

    await client.patch(
        f"/api/v1/connections/{connection_id}",
        json={"nombre": "Conexión 2"},
        headers=auth_headers_admin,
    )

    r3 = await client.patch(
        f"/api/v1/connections/{connection_id}",
        json={"nombre": "Conexión 3"},
        headers={**auth_headers_admin, "If-Match": etag_viejo},
    )
    assert r3.status_code == 412
    assert r3.json()["error"]["codigo"] == "PRECONDICION_FALLIDA"
