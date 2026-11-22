"""Pruebas de paginación unificada con conteo exacto y cursor en kanban (KC-BE-09-2, BE-F35)."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_api.paginacion import codificar_cursor, decodificar_cursor
from kallicode_core.comercial import ciclo_de
from kallicode_core.db import sesion_sistema
from kallicode_core.errors import AppError
from sqlalchemy import text


def test_cursor_roundtrip_y_validacion() -> None:
    instante = datetime(2026, 9, 15, 14, 30, 0, tzinfo=UTC)
    token = codificar_cursor(instante, "KC-1042")

    ts_dec, num_dec = decodificar_cursor(token)
    assert ts_dec == instante
    assert num_dec == "KC-1042"

    with pytest.raises(AppError) as exc:
        decodificar_cursor("cursor-invalido-%%%")
    assert getattr(exc.value, "status_code", getattr(exc.value, "codigo_http", 422)) == 422
    assert exc.value.codigo == "VALIDACION_ENTRADA"


@pytest_asyncio.fixture
async def tenant_paginacion():
    """Tenant aislado con suscripción activa para validar paginación de consumo y kanban."""
    tenant = f"org_pag_{uuid.uuid4().hex[:8]}"
    user_id = f"u_{tenant}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Paginación Test')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner', 'owner')"
            ),
            {"u": user_id, "t": tenant, "e": f"owner@{tenant}.test"},
        )
        await db.execute(
            text(
                """
                INSERT INTO core.subscriptions
                    (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
                VALUES
                    (:s, :t, 'growth', 'activa', current_date - 40, current_date + 20)
                """
            ),
            {"s": f"sub_{tenant}", "t": tenant},
        )
        await db.commit()

    anterior = app.dependency_overrides.copy()
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=user_id, tenant_id=tenant, rol="owner", restringido=False
    )

    try:
        yield tenant
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anterior)
        async with sesion_sistema() as db:
            await db.execute(text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": tenant})
            await db.execute(
                text("DELETE FROM core.ticket_counters WHERE tenant_id = :t"), {"t": tenant}
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("DELETE FROM audit.audit_events WHERE tenant_id = :t"), {"t": tenant}
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"), {"t": tenant}
            )
            await db.execute(
                text("DELETE FROM core.subscriptions WHERE tenant_id = :t"), {"t": tenant}
            )
            await db.execute(text("DELETE FROM core.users WHERE tenant_id = :t"), {"t": tenant})
            await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant})
            await db.commit()


@pytest.mark.asyncio
@pytest.mark.integracion
async def test_usage_by_ticket_total_coincide_con_ciclo_filtrado_y_ultima_pagina_no_vacia(
    client, tenant_paginacion
) -> None:
    """Crea tickets en dos ciclos y verifica que el total de /usage/by-ticket
    coincide con el conjunto filtrado y la última página nunca viene vacía."""
    ahora = datetime.now(UTC)
    ciclo_pasado_ts = ahora - timedelta(days=35)

    async with sesion_sistema() as db:
        ciclo_actual = await ciclo_de(db, tenant_paginacion)
        ciclo_anterior = await ciclo_de(db, tenant_paginacion, ciclo_pasado_ts.date())
        assert ciclo_anterior != ciclo_actual

        # 5 tickets en el ciclo anterior
        for idx in range(5):
            await db.execute(
                text(
                    """
                    INSERT INTO core.tickets
                        (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en, actualizado_en)
                    VALUES
                        (:id, :t, :titulo, 'desc', 'mejora', 'media', 'build', :c, :c)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": tenant_paginacion,
                    "titulo": f"Ticket ciclo anterior {idx}",
                    "c": ciclo_pasado_ts + timedelta(minutes=idx),
                },
            )

        # 4 tickets adicionales en el ciclo actual (total acumulado hasta ciclo actual = 9)
        for idx in range(4):
            await db.execute(
                text(
                    """
                    INSERT INTO core.tickets
                        (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en, actualizado_en)
                    VALUES
                        (:id, :t, :titulo, 'desc', 'bug', 'media', 'triage', :c, :c)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": tenant_paginacion,
                    "titulo": f"Ticket ciclo actual {idx}",
                    "c": ahora - timedelta(minutes=10 - idx),
                },
            )
        await db.commit()

    page_size = 2
    r_p1 = await client.get(
        "/api/v1/usage/by-ticket",
        params={"ciclo": ciclo_anterior, "page": 1, "page_size": page_size},
    )
    assert r_p1.status_code == 200, r_p1.text
    datos_p1 = r_p1.json()
    assert datos_p1["total"] == 5

    ultima_pagina = math.ceil(datos_p1["total"] / page_size)
    assert ultima_pagina == 3

    r_ultima = await client.get(
        "/api/v1/usage/by-ticket",
        params={"ciclo": ciclo_anterior, "page": ultima_pagina, "page_size": page_size},
    )
    assert r_ultima.status_code == 200, r_ultima.text
    datos_ultima = r_ultima.json()
    assert datos_ultima["total"] == 5
    assert len(datos_ultima["items"]) == 1  # Nunca vacía


@pytest.mark.asyncio
@pytest.mark.integracion
async def test_kanban_informa_total_real_y_pagina_con_cursor_30_tickets(
    client, tenant_paginacion
) -> None:
    """Crea 30 tickets en una etapa y afirma que el kanban informa el total real
    y permite recuperar el resto con el cursor de continuación."""
    base = datetime.now(UTC) - timedelta(hours=2)
    async with sesion_sistema() as db:
        for idx in range(30):
            await db.execute(
                text(
                    """
                    INSERT INTO core.tickets
                        (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en, actualizado_en)
                    VALUES
                        (:id, :t, :titulo, 'desc', 'mejora', 'media', 'build', :c, :c)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": tenant_paginacion,
                    "titulo": f"Tarjeta build {idx:02d}",
                    "c": base + timedelta(seconds=idx),
                },
            )
        await db.commit()

    # Primera llamada sin cursor: devuelve las 20 primeras tarjetas y total = 30
    r1 = await client.get("/api/v1/dashboard/kanban")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["totales"]["build"] == 30
    assert len(d1["columnas"]["build"]) == 20
    cursor_build = d1["cursores"]["build"]
    assert cursor_build is not None

    # Segunda llamada con etapa=build y cursor: recupera las 10 restantes sin duplicados
    r2 = await client.get(
        "/api/v1/dashboard/kanban",
        params={"etapa": "build", "cursor": cursor_build},
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["totales"]["build"] == 30
    assert len(d2["columnas"]["build"]) == 10
    assert d2["cursores"]["build"] is None
    assert d2["next_cursor"] is None

    ids_p1 = [t["id"] for t in d1["columnas"]["build"]]
    ids_p2 = [t["id"] for t in d2["columnas"]["build"]]
    assert len(set(ids_p1 + ids_p2)) == 30
