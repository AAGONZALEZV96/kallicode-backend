"""Tiempo e2e de fábrica y orden en el consumo por ticket (KC-PT-09-2, §15.1–15.2).

- GET /usage/by-ticket trae tiempo_e2e_s por ticket (de la creación al cierre, o hasta ahora si
  sigue en curso) y se puede ordenar por él; `sort` devuelve el campo de la API y `total` cuenta
  con la misma condición que la lista.
- GET /usage/summary trae la mediana del tiempo e2e de los tickets que llegaron a producción en
  el ciclo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.comercial import ciclo_actual
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

HORA = 3600


@pytest_asyncio.fixture
async def tenant_con_tickets():
    """Tres tickets cerrados en el ciclo (1 h, 2 h y 10 h), uno en curso (~3 h) y uno cancelado."""
    tenant = f"org_cons_{uuid.uuid4().hex[:8]}"
    ahora = datetime.now(UTC)
    # Todo dentro del ciclo en curso: si hoy es día 1, «hace 10 h» sería el mes anterior.
    inicio_mes = ahora.replace(day=1, hour=0, minute=0, second=1, microsecond=0)
    base = max(ahora - timedelta(hours=12), inicio_mes)
    tickets = {
        "rapido": ("produccion", base, base + timedelta(hours=1)),
        "medio": ("produccion", base, base + timedelta(hours=2)),
        "lento": ("produccion", base, base + timedelta(hours=10)),
        "en_curso": ("qa", max(ahora - timedelta(hours=3), inicio_mes), None),
        "cancelado": ("cancelado", base, base + timedelta(minutes=30)),
    }
    numeros = {}
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Consumo e2e')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner', 'owner')"
            ),
            {"u": f"u_{tenant}", "t": tenant, "e": f"owner@{tenant}.test"},
        )
        for clave, (etapa, creado, cerrado) in tickets.items():
            numeros[clave] = (
                await db.execute(
                    text(
                        """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                               prioridad, etapa, creado_en, actualizado_en, cerrado_en,
                               cancelado_por, motivo_cancelacion)
                           VALUES (:id, :t, :titulo, 'x', 'bug', 'media', :e, :c, :a, :cer,
                               CASE WHEN :e = 'cancelado' THEN :u END,
                               CASE WHEN :e = 'cancelado' THEN 'Ya no hace falta' END)
                           RETURNING numero"""
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "t": tenant,
                        "titulo": f"Ticket {clave}",
                        "e": etapa,
                        "c": creado,
                        # Actualizado después del cierre (un comentario, una firma tardía): el
                        # tiempo e2e se mide hasta cerrado_en, no hasta la última actualización.
                        "a": (cerrado + timedelta(hours=5)) if cerrado else ahora,
                        "cer": cerrado,
                        "u": f"u_{tenant}",
                    },
                )
            ).scalar_one()
        await db.commit()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=f"u_{tenant}", tenant_id=tenant, rol="owner", restringido=False
    )
    yield {
        "tenant": tenant,
        "numeros": numeros,
        "en_curso_desde": tickets["en_curso"][1],
    }
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


async def test_cada_ticket_trae_su_tiempo_e2e(client, tenant_con_tickets) -> None:
    r = await client.get("/api/v1/usage/by-ticket")

    assert r.status_code == 200, r.text
    por_numero = {i["ticket_id"]: i["tiempo_e2e_s"] for i in r.json()["items"]}
    n = tenant_con_tickets["numeros"]
    assert por_numero[n["rapido"]] == HORA
    assert por_numero[n["medio"]] == 2 * HORA
    assert por_numero[n["lento"]] == 10 * HORA
    assert por_numero[n["cancelado"]] == 30 * 60
    # En curso: el tiempo hasta ahora.
    esperado = (datetime.now(UTC) - tenant_con_tickets["en_curso_desde"]).total_seconds()
    assert abs(por_numero[n["en_curso"]] - esperado) <= 60
    assert all(isinstance(v, int) for v in por_numero.values())


@pytest.mark.parametrize(
    ("sort", "orden"),
    [
        ("tiempo_e2e", ["cancelado", "rapido", "medio", "en_curso", "lento"]),
        ("-tiempo_e2e", ["lento", "en_curso", "medio", "rapido", "cancelado"]),
    ],
)
async def test_se_ordena_por_tiempo_e2e(client, tenant_con_tickets, sort, orden) -> None:
    r = await client.get("/api/v1/usage/by-ticket", params={"sort": sort})

    assert r.status_code == 200, r.text
    n = tenant_con_tickets["numeros"]
    assert [i["ticket_id"] for i in r.json()["items"]] == [n[c] for c in orden]
    # Devuelve el campo de la API, no la expresión SQL.
    assert r.json()["sort"] == sort


async def test_sort_por_defecto_y_total_con_la_misma_condicion(client, tenant_con_tickets) -> None:
    r = await client.get("/api/v1/usage/by-ticket", params={"page_size": 2})

    assert r.json()["sort"] == "-actualizado_en"
    assert r.json()["total"] == 5
    assert len(r.json()["items"]) == 2


async def test_un_ciclo_anterior_no_cuenta_los_tickets_creados_despues(
    client, tenant_con_tickets
) -> None:
    ciclo = ciclo_actual()
    anio, mes = int(ciclo[:4]), int(ciclo[5:])
    anterior = f"{anio - 1}-12" if mes == 1 else f"{anio}-{mes - 1:02d}"

    r = await client.get("/api/v1/usage/by-ticket", params={"ciclo": anterior})

    assert r.status_code == 200, r.text
    # La lista y el total usan la misma condición (antes total contaba todo el tenant).
    assert (r.json()["items"], r.json()["total"]) == ([], 0)


async def test_un_campo_de_orden_desconocido_responde_422(client, tenant_con_tickets) -> None:
    r = await client.get("/api/v1/usage/by-ticket", params={"sort": "tiempo_e2e_s; DROP"})

    assert r.status_code == 422, r.text
    assert "tiempo_e2e" in r.json()["error"]["detalle"]["campos_permitidos"]


async def test_el_resumen_trae_la_mediana_de_los_cerrados_del_ciclo(
    client, tenant_con_tickets
) -> None:
    r = await client.get("/api/v1/usage/summary", params={"ciclo": ciclo_actual()})

    assert r.status_code == 200, r.text
    # Cerrados en producción: 1 h, 2 h y 10 h → mediana 2 h (el cancelado y el en curso no).
    assert r.json()["tiempo_e2e"] == {"mediana_s": 2 * HORA, "cerrados": 3}
