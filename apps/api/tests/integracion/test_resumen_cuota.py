"""Almacenamiento y cola en el resumen de consumo (KC-PT-09-5, banners de cuota).

- GET /usage/summary trae `almacenamiento` con los mismos umbrales que bloquean la subida
  (comercial.verificar_storage, QU-3): aviso desde el 80 %, agotado al llegar al límite. Sin
  límite en el plan, limite_bytes y porcentaje son null.
- `cola` cuenta los tickets que esperan en en_cola_por_cuota (sin los eliminados) y desde cuándo
  espera el más antiguo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.comercial import verificar_storage
from kallicode_core.db import sesion_sistema
from kallicode_core.errors import AppError
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

MIB = 1024 * 1024
# Un plan propio con 10 MiB de almacenamiento: los umbrales caen en bytes exactos.
LIMITE_MB = 10


@pytest_asyncio.fixture
async def tenant():
    """Organización con un plan propio (10 MiB) y su suscripción vigente."""
    t = f"org_res_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        for sql in (
            "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Resumen de cuota')",
            """INSERT INTO core.plans (codigo, nombre, lineas, loc_mes, storage_mb)
               VALUES (:t, 'Plan de prueba', 1, 1000, :mb)""",
            """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el,
                   renueva_el)
               VALUES (:t, :t, :t, 'activa', current_date - 5, current_date + 25)""",
            """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
               VALUES (:t, :t, :t || '@example.test', 'Owner', 'owner')""",
        ):
            await db.execute(text(sql), {"t": t, "mb": LIMITE_MB})

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=t, tenant_id=t, rol="owner", restringido=False
    )
    yield t
    app.dependency_overrides.clear()

    async with sesion_sistema() as db:
        for tabla in ("core.ticket_attachments", "core.tickets", "core.ticket_counters"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.notifications WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.subscriptions WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.users WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.plans WHERE codigo=:t"), {"t": t})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": t})


async def _adjuntos(t: str, *tamanos: int) -> None:
    """Adjuntos del tenant que suman esos bytes (cada uno ≤ 50 MiB por att_tamano_chk)."""
    async with sesion_sistema() as db:
        for tamano in tamanos:
            await db.execute(
                text(
                    """INSERT INTO core.ticket_attachments (id, tenant_id, nombre_archivo,
                           content_type, tamano_bytes, blob_ref)
                       VALUES (:id, :t, 'a.png', 'image/png', :b, 'x/' || :id)"""
                ),
                {"id": f"att_{uuid.uuid4().hex}", "t": t, "b": tamano},
            )


async def _resumen(client) -> dict:
    r = await client.get("/api/v1/usage/summary")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    ("usados", "umbral", "porcentaje"),
    [
        (0, "normal", 0.0),
        # Justo bajo el 80 %: normal; desde el 80 % exacto, aviso.
        (8 * MIB - 1, "normal", 80.0),
        (8 * MIB, "aviso_80", 80.0),
        (10 * MIB - 1, "aviso_80", 100.0),
        # Al llegar al límite la subida ya responde ALMACENAMIENTO_AGOTADO.
        (10 * MIB, "agotado", 100.0),
    ],
)
async def test_almacenamiento_con_los_umbrales_de_la_subida(
    client, tenant, usados, umbral, porcentaje
) -> None:
    if usados:
        await _adjuntos(
            tenant, *([MIB] * (usados // MIB)), *([usados % MIB] if usados % MIB else [])
        )

    assert (await _resumen(client))["almacenamiento"] == {
        "usados_bytes": usados,
        "limite_bytes": LIMITE_MB * MIB,
        "porcentaje": porcentaje,
        "umbral": umbral,
    }


@pytest.mark.parametrize("usados", [8 * MIB - 1, 8 * MIB, 10 * MIB - 1, 10 * MIB])
async def test_el_umbral_coincide_con_el_que_bloquea_la_subida(client, tenant, usados) -> None:
    """Lo que muestra el portal es lo que hace verificar_storage con una subida de 0 bytes."""
    await _adjuntos(tenant, *([MIB] * (usados // MIB)), *([usados % MIB] if usados % MIB else []))
    umbral = (await _resumen(client))["almacenamiento"]["umbral"]

    async with sesion_sistema() as db:
        try:
            aviso = await verificar_storage(db, tenant, 0)
            real = "aviso_80" if aviso else "normal"
        except AppError as e:
            assert e.codigo == "ALMACENAMIENTO_AGOTADO"
            real = "agotado"
        await db.rollback()

    assert umbral == real


async def test_un_plan_sin_limite_de_almacenamiento(client, tenant) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text("UPDATE core.plans SET storage_mb = NULL WHERE codigo=:t"), {"t": tenant}
        )
    await _adjuntos(tenant, 3 * MIB)

    assert (await _resumen(client))["almacenamiento"] == {
        "usados_bytes": 3 * MIB,
        "limite_bytes": None,
        "porcentaje": None,
        "umbral": "normal",
    }


async def test_la_cola_cuenta_los_tickets_en_espera_y_el_mas_antiguo(client, tenant) -> None:
    ahora = datetime.now(UTC).replace(microsecond=0)
    async with sesion_sistema() as db:
        for etapa, hace, eliminado in (
            ("en_cola_por_cuota", timedelta(days=2), False),
            ("en_cola_por_cuota", timedelta(hours=3), False),
            # Uno eliminado (más antiguo) y otros fuera de la cola no cuentan.
            ("en_cola_por_cuota", timedelta(days=9), True),
            ("triage", timedelta(days=20), False),
            ("qa", timedelta(days=30), False),
        ):
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                           prioridad, etapa, creado_en, deleted_at)
                       VALUES (:id, :t, 'T', 'x', 'bug', 'media', :e, :c,
                           CASE WHEN :borrado THEN now() END)"""
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": tenant,
                    "e": etapa,
                    "c": ahora - hace,
                    "borrado": eliminado,
                },
            )

    cola = (await _resumen(client))["cola"]

    assert cola["tickets"] == 2
    assert datetime.fromisoformat(cola["desde"]) == ahora - timedelta(days=2)


async def test_sin_tickets_en_cola(client, tenant) -> None:
    assert (await _resumen(client))["cola"] == {"tickets": 0, "desde": None}
