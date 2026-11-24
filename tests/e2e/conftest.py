"""Fixtures para los tests E2E del pipeline (KC-BE-06-6)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_api.servicios import boveda
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

GRABACIONES = Path(__file__).parent / "grabaciones"
SECRETO_WEBHOOK = "secreto-test-e2e-kc-be-06-6"  # pragma: allowlist secret - secreto sintético exclusivo de pruebas


@pytest_asyncio.fixture
async def seed_tenant_y_conexion():
    tenant_id = str(uuid.uuid4())
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    connection_id = f"cn_{uuid.uuid4().hex[:8]}"
    ruta_token = f"wh_{uuid.uuid4().hex[:16]}"
    plan_codigo = f"pro_e2e_{uuid.uuid4().hex[:12]}"

    async with sesion_sistema() as db:
        # 1. Organización (con fabrica_activa=true para que el pipeline arranque)
        await db.execute(
            text("""
                INSERT INTO core.organizations (id, nombre, fabrica_activa)
                VALUES (:id, 'Tenant E2E', true)
            """),
            {"id": tenant_id},
        )
        # 2. Plan (permite_help=true, resto de campos NOT NULL)
        await db.execute(
            text("""
                INSERT INTO core.plans (codigo, nombre, permite_help)
                VALUES (:codigo, 'Pro E2E', true)
            """),
            {"codigo": plan_codigo},
        )
        # 3. Suscripción (inicia_el y renueva_el NOT NULL, renueva_el > inicia_el)
        await db.execute(
            text("""
                INSERT INTO core.subscriptions
                    (id, tenant_id, plan_codigo, inicia_el, renueva_el)
                VALUES (:id, :t, :codigo, current_date, current_date + 30)
            """),
            {"id": f"sub_{uuid.uuid4().hex[:8]}", "t": tenant_id, "codigo": plan_codigo},
        )
        # 4. Usuario aprobador (rol approver)
        await db.execute(
            text("""
                INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
                VALUES (:id, :t, :email, 'Aprobador', 'approver', 'activo')
            """),
            {"id": user_id, "t": tenant_id, "email": f"{user_id}@{tenant_id[:8]}.test"},
        )
        # 5. Conexión (categoria ∈ {repositorio, tickets, cicd, database})
        await db.execute(
            text("""
                INSERT INTO core.connections
                    (id, tenant_id, nombre, proveedor, categoria, estado, ruta_token)
                VALUES
                    (:id, :t, 'GitHub E2E', 'github', 'repositorio', 'conectada', :rt)
            """),
            {"id": connection_id, "t": tenant_id, "rt": ruta_token},
        )
        await db.commit()

    # 6. Secreto del webhook en la bóveda
    await boveda.guardar(tenant_id, f"cn--{connection_id}", {"webhook_secret": SECRETO_WEBHOOK})

    yield {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "connection_id": connection_id,
        "ruta_token": ruta_token,
    }

    await boveda.eliminar(tenant_id, f"cn--{connection_id}")

    # Limpieza limitada al tenant de esta prueba.
    async with sesion_sistema() as db:
        # El ticket elimina firmas, pasos, specs, jobs y consumo por cascada,
        # respetando la protección append-only de las tablas probatorias.
        for tabla in (
            "core.webhook_inbox",
            "core.tickets",
            "core.production_lines",
            "core.idempotency_keys",
            "core.ticket_counters",
            "core.connections",
            "core.users",
            "core.subscriptions",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant_id})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant_id}
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.plans WHERE codigo=:c"), {"c": plan_codigo})
        await db.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def cliente_orquestador():
    from kallicode_api.main_interna import app as app_interna
    from kallicode_workers.orquestador.cliente_interno import ClienteInterno

    cliente = ClienteInterno()
    await cliente._http.aclose()
    cliente._http = AsyncClient(
        transport=ASGITransport(app=app_interna), base_url="http://test/internal/v1"
    )
    try:
        yield cliente
    finally:
        await cliente.cerrar()


@pytest_asyncio.fixture(autouse=True)
async def servicios_grabados(monkeypatch):
    """Sustituye solo proveedores externos; conserva cliente LLM, etapas y APIs reales."""
    from contextlib import asynccontextmanager
    from dataclasses import replace
    from types import SimpleNamespace

    from kallicode_core import adjuntos
    from kallicode_core.config import get_settings
    from kallicode_core.grafo import GrafoClienteDoble
    from kallicode_core.llm import cliente as llm_cliente
    from kallicode_core.llm.plantillas import PLANTILLAS
    from kallicode_workers.orquestador import _comun, etapas

    from . import llm_demo

    monkeypatch.setattr(get_settings(), "persistir_tier_fable", True)
    monkeypatch.setattr(llm_cliente, "_circuitos", {})
    llamadas = []
    transporte_llm = llm_cliente._llamar

    async def proveedor_grabado(cli, cfg, system, user):
        candidatos = [p for p, pl in PLANTILLAS.items() if system.startswith(pl["system"])]
        assert len(candidatos) == 1, candidatos
        paso = candidatos[0]
        llamadas.append((paso, cfg.tier.value))
        if paso.startswith(("triage.", "design.")):
            return await transporte_llm(
                demo,
                replace(cfg, base_url="http://test/v1", modelo=f"demo-{cfg.tier.value}"),
                system,
                user,
            )
        archivo = GRABACIONES / f"{paso.replace('.', '_')}_{cfg.tier.value}.json"
        salida = archivo.read_text()
        return salida, 64, 32

    monkeypatch.setattr(llm_cliente, "_llamar", proveedor_grabado)
    monkeypatch.setattr(etapas, "grafo_cliente", GrafoClienteDoble)
    artefactos = {}

    class Blob:
        def __init__(self, ref):
            self.ref = ref

        async def upload_blob(self, contenido, **kwargs):
            artefactos[self.ref] = contenido

        async def download_blob(self):
            return self

        async def readall(self):
            return artefactos[self.ref]

    class Almacen:
        def get_container_client(self, nombre):
            return self

        async def create_container(self):
            pass

        def get_blob_client(self, *args):
            return Blob(args[-1])

    @asynccontextmanager
    async def blob_local():
        yield Almacen()

    monkeypatch.setattr(_comun, "cliente_blob", blob_local)
    monkeypatch.setattr(adjuntos, "cliente_blob", blob_local)
    async with AsyncClient(transport=ASGITransport(app=llm_demo.app)) as demo:
        yield SimpleNamespace(llamadas=llamadas, artefactos=artefactos)
