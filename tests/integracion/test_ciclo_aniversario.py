"""Pruebas de integración de facturación por aniversario de suscripción (KC-BE-09-1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, UsuarioActual, servicio_actual, usuario_actual
from kallicode_api.main import app
from kallicode_core.comercial import ciclo_de, estado_cuota_loc
from kallicode_core.db import sesion_sistema, todos, uno
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


async def _limpiar_tenant(tenant: str) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text("ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only")
        )
        await db.execute(text("DELETE FROM core.usage_loc WHERE tenant_id = :t"), {"t": tenant})
        await db.execute(
            text("ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only")
        )
        for tabla in (
            "core.usage_cycles",
            "core.notifications",
            "core.tickets",
            "core.ticket_counters",
            "core.subscriptions",
            "core.users",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id = :t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant})
        await db.commit()


async def test_ciclo_aniversario_mitad_de_mes_consumo_y_validacion() -> None:
    """Suscripción iniciada el día 15: consumo antes y después del aniversario cae en el
    ciclo correcto, /usage/summary y /usage/by-ticket cuadran y un ciclo futuro da 422."""
    sfx = uuid.uuid4().hex[:8]
    tenant = f"org_aniv_{sfx}"
    user_id = f"u_{tenant}"
    plan_codigo = f"plan_aniv_{sfx}"
    sub_id = f"sub_{sfx}"

    antes_aniv = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)
    despues_aniv = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org Aniversario')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Aniv', 'owner')"
            ),
            {"u": user_id, "t": tenant, "e": f"owner@{tenant}.test"},
        )
        await db.execute(
            text(
                "INSERT INTO core.plans (codigo, nombre, loc_mes, lineas) "
                "VALUES (:p, 'Plan Aniv', 10000, 3)"
            ),
            {"p": plan_codigo},
        )
        await db.execute(
            text(
                """
                INSERT INTO core.subscriptions (
                    id, tenant_id, plan_codigo, estado, inicia_el, renueva_el
                )
                VALUES (:s, :t, :p, 'activa', DATE '2026-01-15', DATE '2026-12-15')
                """
            ),
            {"s": sub_id, "t": tenant, "p": plan_codigo},
        )
        await db.commit()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=user_id, tenant_id=tenant, rol="owner", restringido=False
    )
    app.dependency_overrides[servicio_actual] = lambda: ServicioActual(
        servicio="worker", tenant_id=tenant
    )

    try:
        async with sesion_sistema() as db:
            ciclo_antes = await ciclo_de(db, tenant, antes_aniv)
            ciclo_despues = await ciclo_de(db, tenant, despues_aniv)

            assert ciclo_antes == "2026-10"
            assert ciclo_despues == "2026-11"

            tk_antes = await uno(
                db,
                """
                INSERT INTO core.tickets (
                    id, tenant_id, titulo, descripcion, tipo, prioridad,
                    etapa, creado_en, actualizado_en, cerrado_en
                )
                VALUES (
                    :id, :t, 'Ticket antes aniversario', 'desc', 'bug', 'media',
                    'produccion', :ts, :ts, :ts
                )
                RETURNING id, numero
                """,
                {"id": str(uuid.uuid4()), "t": tenant, "ts": antes_aniv},
            )
            tk_despues = await uno(
                db,
                """
                INSERT INTO core.tickets (
                    id, tenant_id, titulo, descripcion, tipo, prioridad,
                    etapa, creado_en, actualizado_en, cerrado_en
                )
                VALUES (
                    :id, :t, 'Ticket despues aniversario', 'desc', 'feature', 'media',
                    'produccion', :ts, :ts, :ts
                )
                RETURNING id, numero
                """,
                {"id": str(uuid.uuid4()), "t": tenant, "ts": despues_aniv},
            )
            assert tk_antes and tk_despues

            await db.execute(
                text(
                    """
                    INSERT INTO core.usage_loc (
                        tenant_id, ticket_id, ciclo, tipo, pr_url,
                        anadidas, eliminadas, netas, tests, creado_en
                    )
                    VALUES (:t, :tk, :c, 'pr', :pr, 1500, 300, 1200, 5, :ts)
                    """
                ),
                {
                    "t": tenant,
                    "tk": tk_antes["id"],
                    "c": ciclo_antes,
                    "pr": f"https://github.com/org/repo/pull/{sfx}-1",
                    "ts": antes_aniv,
                },
            )

            await db.execute(
                text(
                    """
                    INSERT INTO core.usage_loc (
                        tenant_id, ticket_id, ciclo, tipo, pr_url,
                        anadidas, eliminadas, netas, tests, creado_en
                    )
                    VALUES (:t, :tk, :c, 'pr', :pr, 2800, 300, 2500, 8, :ts)
                    """
                ),
                {
                    "t": tenant,
                    "tk": tk_despues["id"],
                    "c": ciclo_despues,
                    "pr": f"https://github.com/org/repo/pull/{sfx}-2",
                    "ts": despues_aniv,
                },
            )

            await db.execute(
                text(
                    """
                    UPDATE core.subscriptions
                       SET inicia_el = make_date(
                               extract(year FROM current_date)::int,
                               extract(month FROM current_date)::int,
                               least(28, extract(day FROM current_date)::int + 1)
                           )
                     WHERE id = :s
                    """
                ),
                {"s": sub_id},
            )
            await db.commit()

            cuota_oct = await estado_cuota_loc(db, tenant, ciclo="2026-10")
            cuota_nov = await estado_cuota_loc(db, tenant, ciclo="2026-11")
            assert cuota_oct["consumidas"] == 1200
            assert cuota_nov["consumidas"] == 2500

            lineas = await todos(
                db,
                "SELECT ciclo, netas FROM core.usage_loc WHERE tenant_id = :t ORDER BY ciclo",
                {"t": tenant},
            )
            assert [(l["ciclo"], l["netas"]) for l in lineas] == [
                ("2026-10", 1200),
                ("2026-11", 2500),
            ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_oct = await client.get("/api/v1/usage/summary", params={"ciclo": "2026-10"})
            assert r_oct.status_code == 200, r_oct.text
            body_oct = r_oct.json()
            assert body_oct["ciclo"] == "2026-10"
            assert body_oct["loc"]["consumidas"] == 1200
            assert body_oct["loc"]["restantes"] == 8800

            r_bt_oct = await client.get("/api/v1/usage/by-ticket", params={"ciclo": "2026-10"})
            assert r_bt_oct.status_code == 200, r_bt_oct.text
            items_oct = r_bt_oct.json()["items"]
            assert len(items_oct) == 1
            assert items_oct[0]["ticket_id"] == tk_antes["numero"]
            assert items_oct[0]["loc_netas"] == 1200

            r_fut = await client.get("/api/v1/usage/summary", params={"ciclo": "2099-01"})
            assert r_fut.status_code == 422, r_fut.text
            assert r_fut.json()["error"]["codigo"] == "CICLO_INVALIDO"

            r_fmt = await client.get("/api/v1/usage/by-ticket", params={"ciclo": "2026-13"})
            assert r_fmt.status_code == 422, r_fmt.text
    finally:
        app.dependency_overrides.clear()
        await _limpiar_tenant(tenant)
        async with sesion_sistema() as db:
            await db.execute(text("DELETE FROM core.plans WHERE codigo = :p"), {"p": plan_codigo})
            await db.commit()
