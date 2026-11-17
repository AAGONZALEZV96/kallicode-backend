"""Fixtures para los tests de integración de KC-BE-06-2 y KC-BE-06-4."""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_api.main_interna import app as app_interna
from kallicode_core.db import sesion_sistema
from sqlalchemy import text


@pytest_asyncio.fixture
async def db_admin():
    async with sesion_sistema() as db:
        yield db
        await db.rollback()


@pytest_asyncio.fixture
async def seed_dos_tenants():
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant A Test')"),
            {"id": id_a},
        )
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant B Test')"),
            {"id": id_b},
        )
        await db.commit()

    # ESTE YIELD ES LO QUE RECIBE EL TEST
    yield id_a, id_b

    # Teardown en orden inverso a las FKs.
    async with sesion_sistema() as db:
        # qa_results y qa_runs son append-only: desactivar triggers de usuario para limpiar.
        await db.execute(text("ALTER TABLE core.qa_results DISABLE TRIGGER USER"))
        await db.execute(
            text("DELETE FROM core.qa_results WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(text("ALTER TABLE core.qa_results ENABLE TRIGGER USER"))

        await db.execute(text("ALTER TABLE core.qa_runs DISABLE TRIGGER USER"))
        await db.execute(
            text("DELETE FROM core.qa_runs WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(text("ALTER TABLE core.qa_runs ENABLE TRIGGER USER"))

        await db.execute(
            text("DELETE FROM core.escalations WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.jobs WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.specs WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.users WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.tickets WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.ticket_counters WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("DELETE FROM audit.audit_events WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("DELETE FROM audit.chain_heads WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.organizations WHERE id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.commit()


@pytest_asyncio.fixture
async def crear_ticket():
    async def _crear(tenant_id: str, etapa: str = "triage", titulo: str = "Ticket de prueba"):
        ticket_id = str(uuid.uuid4())
        async with sesion_sistema() as db:
            resultado = await db.execute(
                text(
                    """INSERT INTO core.tickets
                       (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en, cerrado_en)
                       VALUES (:id, :t, :titulo, 'Descripción de prueba', 'bug', 'media', :etapa, now(),
                               CASE WHEN :etapa IN ('produccion', 'cerrado_duplicado') THEN now() END)
                       RETURNING numero, tenant_id, etapa"""
                ),
                {"id": ticket_id, "t": tenant_id, "titulo": titulo, "etapa": etapa},
            )
            await db.commit()
            return dict(resultado.mappings().one())

    return _crear


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP contra la app pública (main.py)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_interna():
    """Cliente HTTP contra la app interna (main_interna.py), donde vive /internal/v1."""
    async with AsyncClient(transport=ASGITransport(app=app_interna), base_url="http://test") as c:
        yield c
