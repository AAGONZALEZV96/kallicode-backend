"""Pruebas de humo para KC-BE-01-1: confirman que la separación de
roles (kallicode_app / kallicode_admin) no rompe los flujos de la API
que usan sesion_sistema() antes de resolver tenant.

Cubre lo que SÍ es verificable por HTTP: login, refresh, recepción de
webhooks. Lo que necesita inspección directa de PostgreSQL
(current_user, rolbypassrls) ya está cubierto en
packages/core/tests/test_db.py — aquí solo probamos que el flujo
completo responde con los códigos esperados, no que use el rol
correcto (eso ya está probado en otro lado).

Requiere PostgreSQL y Redis reales.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# AJUSTAR: importa tu app real. Puede ser algo como:
#   from kallicode_api.main import app
# o si usas un factory:
#   from kallicode_api.main import create_app
#   app = create_app()
from kallicode_api.main import app
from kallicode_core import db
from kallicode_core.seguridad import hashear_password
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


@pytest.fixture
async def cliente():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def usuario_de_prueba():
    """Crea un usuario de prueba con el esquema real y lo elimina al terminar."""
    correo = "humo-kc-be-01-1@kallicode.dev"
    password = "PasswordDePrueba123!"

    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("DELETE FROM core.users WHERE id = 'u_humo_be_01_1'"),
        )
        await admin.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) VALUES "
                "('org_tenant_a', 'Tenant humo') ON CONFLICT DO NOTHING"
            ),
        )
        await admin.execute(
            text(
                "INSERT INTO core.users "
                "(id, tenant_id, email, nombre, password_hash, rol, estado) "
                "VALUES ('u_humo_be_01_1', 'org_tenant_a', :email, 'Usuario humo', "
                ":password_hash, 'admin', 'activo')"
            ),
            {"email": correo, "password_hash": hashear_password(password)},
        )

    try:
        yield {"email": correo, "password": password}
    finally:
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("DELETE FROM core.users WHERE email = :email"),
                {"email": correo},
            )


async def test_login_funciona_con_separacion_de_roles(cliente, usuario_de_prueba):
    """Login pasa por sesion_sistema() (antes de resolver tenant).
    Si la separación de roles rompió algo, esto falla con 500."""
    respuesta = await cliente.post(
        "/api/v1/auth/login",
        json={
            "email": usuario_de_prueba["email"],
            "password": usuario_de_prueba["password"],
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert "access_token" in cuerpo
    assert "refresh_token" in cuerpo


async def test_refresh_funciona_con_separacion_de_roles(cliente, usuario_de_prueba):
    login = await cliente.post(
        "/api/v1/auth/login",
        json={
            "email": usuario_de_prueba["email"],
            "password": usuario_de_prueba["password"],
        },
    )
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]

    respuesta = await cliente.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert "access_token" in respuesta.json()


async def test_webhook_se_recibe_con_separacion_de_roles(cliente):
    """Una firma inválida no debe producir un error interno de base de datos."""
    payload = {"evento": "prueba_humo", "ticket_id": "KC-0000"}

    respuesta = await cliente.post(
        "/api/v1/webhooks/kallicode-help/wh_missing",
        json=payload,
        headers={
            "X-Kc-Signature": "firma-invalida",
        },
    )
    assert respuesta.status_code != 500, respuesta.text
