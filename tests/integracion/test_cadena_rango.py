"""Verificación de cadena acotada por tramo de ticket y presupuesto p95 (KC-BE-08-2 / BE-F32)."""

from __future__ import annotations

import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def entorno_cadena_rango():
    tenant = f"org_cad82_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org Cadena KC-BE-08-2')"
            ),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Cadena 082', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        t1_num = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket Acotado', 'Desc 1', 'bug', 'alta', 'triage', :u)
                       RETURNING numero"""
                ),
                {"id": str(uuid.uuid4()), "t": tenant, "u": owner},
            )
        ).scalar_one()
        t2_num = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket Externo', 'Desc 2', 'feature', 'media', 'build', :u)
                       RETURNING numero"""
                ),
                {"id": str(uuid.uuid4()), "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.commit()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=owner, tenant_id=tenant, rol="owner", restringido=False
    )
    yield {"tenant": tenant, "owner": owner, "t1": t1_num, "t2": t2_num}
    app.dependency_overrides.clear()

    async with sesion_sistema() as db:
        for tabla in ("core.tickets", "core.ticket_counters", "core.users"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


def test_contrato_declara_distincion_tramo_integro_vs_cadena_tenant() -> None:
    """contratos/api-v1.yaml declara alcance, tramo_integro y cadena_tenant_integra."""
    contrato = yaml.safe_load((ROOT / "contratos/api-v1.yaml").read_text(encoding="utf-8"))
    schemas = contrato["components"]["schemas"]

    exp_int = schemas["ExpedienteIntegridadOut"]["properties"]
    assert "tramo_integro" in exp_int
    assert "cadena_tenant_integra" in exp_int
    assert "alcance" in exp_int

    int_out = schemas["IntegridadOut"]["properties"]
    assert "tramo_integro" in int_out
    assert "cadena_tenant_integra" in int_out
    assert "alcance" in int_out


@pytest.mark.integracion
async def test_corrupcion_fuera_del_tramo_no_invalida_expediente_pero_si_integrity(
    entorno_cadena_rango,
) -> None:
    """Corromper un evento fuera del tramo del ticket mantiene tramo_integro=True en expediente
    y es detectado por /audit/integrity."""
    tenant = entorno_cadena_rango["tenant"]
    owner = entorno_cadena_rango["owner"]
    t1 = entorno_cadena_rango["t1"]
    t2 = entorno_cadena_rango["t2"]

    async with sesion_sistema() as db:
        for i in range(4):
            await registrar_evento(
                db,
                tenant,
                evento="organizacion_modificada",
                resumen=f"Evento previo tenant {i}",
                actor_tipo="sistema",
            )
        for i in range(4):
            await registrar_evento(
                db,
                tenant,
                evento="ticket_creado",
                resumen=f"Evento propio de {t1} #{i}",
                actor_tipo="humano",
                actor_id=owner,
                etapa="triage",
                ticket_id=t1,
            )
        for i in range(4):
            await registrar_evento(
                db,
                tenant,
                evento="deploy_ejecutado",
                resumen=f"Evento externo en {t2} #{i}",
                actor_tipo="agente",
                actor_id="agente-deploy",
                etapa="deploy",
                ticket_id=t2,
            )
        await db.commit()

        ev_fuera = (
            await db.execute(
                text(
                    "SELECT id FROM audit.audit_events "
                    "WHERE tenant_id=:t AND ticket_id=:tk ORDER BY id LIMIT 1"
                ),
                {"t": tenant, "tk": t2},
            )
        ).scalar_one()

        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("UPDATE audit.audit_events SET resumen='ALTERADO_FUERA_TRAMO' WHERE id=:id"),
            {"id": ev_fuera},
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_exp = await client.get(f"/api/v1/audit/tickets/{t1}")
        assert r_exp.status_code == 200, r_exp.text
        integridad = r_exp.json()["integridad"]
        assert integridad["alcance"] == "tramo_ticket"
        assert integridad["tramo_integro"] is True
        assert integridad["integra"] is True
        assert integridad["cadena_tenant_integra"] is None
        assert integridad["eventos_verificados"] == 4
        assert integridad["primer_evento_roto"] is None

        r_int = await client.get("/api/v1/audit/integrity")
        assert r_int.status_code == 200, r_int.text
        global_int = r_int.json()
        assert global_int["alcance"] == "cadena_tenant"
        assert global_int["cadena_tenant_integra"] is False
        assert global_int["integra"] is False
        assert global_int["primer_evento_roto"] == ev_fuera

        async with sesion_sistema() as db:
            ev_dentro = (
                await db.execute(
                    text(
                        "SELECT id FROM audit.audit_events "
                        "WHERE tenant_id=:t AND ticket_id=:tk ORDER BY id LIMIT 1"
                    ),
                    {"t": tenant, "tk": t1},
                )
            ).scalar_one()
            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("UPDATE audit.audit_events SET resumen='ALTERADO_EN_TRAMO' WHERE id=:id"),
                {"id": ev_dentro},
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            await db.commit()

        r_exp_roto = await client.get(f"/api/v1/audit/tickets/{t1}")
        assert r_exp_roto.status_code == 200
        int_roto = r_exp_roto.json()["integridad"]
        assert int_roto["tramo_integro"] is False
        assert int_roto["integra"] is False
        assert int_roto["cadena_tenant_integra"] is None
        assert int_roto["primer_evento_roto"] == ev_dentro


@pytest.mark.integracion
async def test_presupuesto_latencia_p95_expediente_bajo_800ms_con_50k_eventos(
    entorno_cadena_rango,
) -> None:
    """Presupuesto de latencia p95 < 800 ms del expediente sobre un tenant con 50.000 eventos."""
    tenant = entorno_cadena_rango["tenant"]
    t1 = entorno_cadena_rango["t1"]
    base_ts = datetime.now(UTC) - timedelta(days=30)

    async with sesion_sistema() as db:
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal"))
        await db.execute(
            text(
                """
                INSERT INTO audit.audit_events
                    (tenant_id, ticket_id, actor_tipo, evento, resumen,
                     sello_previo, sello, creado_en, version_sello)
                SELECT
                    :t,
                    'KC-HIST-' || (1 + (g % 200)),
                    'sistema',
                    'evento_historico',
                    'Evento histórico ' || g,
                    repeat('0', 64),
                    md5(g::text) || md5((g + 7)::text),
                    :base + ((g::numeric / 50000.0) * interval '29 days'),
                    2
                FROM generate_series(1, 50000) AS g
                """
            ),
            {"t": tenant, "base": base_ts},
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal"))

        for i in range(15):
            await registrar_evento(
                db,
                tenant,
                evento="paso_llm",
                resumen=f"Paso {i} de {t1}",
                actor_tipo="agente",
                actor_id="agente-build",
                etapa="build",
                ticket_id=t1,
            )
        await db.commit()

    latencias_ms: list[float] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_warm = await client.get(f"/api/v1/audit/tickets/{t1}")
        assert r_warm.status_code == 200, r_warm.text
        assert r_warm.json()["integridad"]["tramo_integro"] is True
        assert r_warm.json()["integridad"]["eventos_verificados"] == 15

        for _ in range(25):
            t0 = time.perf_counter()
            r = await client.get(f"/api/v1/audit/tickets/{t1}")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert r.status_code == 200
            latencias_ms.append(elapsed_ms)

    latencias_ordenadas = sorted(latencias_ms)
    idx_p95 = max(0, math.ceil(0.95 * len(latencias_ordenadas)) - 1)
    p95_ms = latencias_ordenadas[idx_p95]
    assert p95_ms < 800.0, (
        f"Latencia p95 del expediente excedió el presupuesto de 800 ms: "
        f"p95={p95_ms:.1f} ms (muestras={latencias_ordenadas})"
    )
