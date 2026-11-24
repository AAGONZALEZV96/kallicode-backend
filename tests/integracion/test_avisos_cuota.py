"""KC-BE-09-3: avisos de cuota y renovación con PostgreSQL."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from kallicode_api.deps import ServicioActual, UsuarioActual
from kallicode_api.routers import internal, tickets
from kallicode_core import comercial
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError
from kallicode_workers import avisos_cuota, billing_cycle
from sqlalchemy import text

pytestmark = pytest.mark.integracion


@pytest.fixture
async def cuota(db_admin, monkeypatch):
    db, tenant = db_admin, f"org_cuota_{uuid4().hex}"
    for sql in (
        "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Prueba de cuotas')",
        """INSERT INTO core.plans (codigo, nombre, lineas, loc_mes, storage_mb)
           VALUES (:t, 'Prueba de cuotas', 3, 100, 1)""",
        """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
           VALUES (:t, :t, :t, 'activa', current_date - 40, current_date + 10)""",
        """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
           VALUES (:t, :t, :t || '@example.test', 'Propietario', 'owner')""",
    ):
        await db.execute(text(sql), {"t": tenant})

    @asynccontextmanager
    async def sesion(*args, **kwargs):
        async with db.begin_nested():
            yield db

    monkeypatch.setattr(internal, "sesion_de", sesion)
    monkeypatch.setattr(avisos_cuota, "sesion_tenant", sesion)
    monkeypatch.setattr(billing_cycle, "sesion_tenant", sesion)
    return db, tenant


async def _ticket(db, tenant, etapa="triage"):
    fila = await uno(
        db,
        """INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, etapa, creado_en)
           VALUES (:id, :t, 'bug', 'Ticket de cuota', 'Detalle', :etapa, now() - interval '3 days')
           RETURNING numero""",
        {"id": uuid4().hex, "t": tenant, "etapa": etapa},
    )
    return fila["numero"]


async def _avisos(db, tenant):
    return await todos(
        db,
        """SELECT titulo, cuerpo FROM core.notifications
           WHERE tenant_id = :t AND user_id = :t AND tipo = 'aviso_cuota'""",
        {"t": tenant},
    )


async def test_avisos_y_corte_al_79_81_y_100_por_ciento(cuota):
    db, tenant = cuota
    numero = await _ticket(db, tenant)
    svc = ServicioActual(servicio="worker", tenant_id=tenant, linea=None)
    mib = 1024 * 1024
    anterior = 0
    esperados = []
    for porcentaje, umbral, nuevos in (
        (79, None, []),
        (
            81,
            "aviso_80",
            [
                "Alcanzaste el 80% de la cuota mensual de LOC.",
                "El almacenamiento de evidencia alcanzó el 80 % del plan.",
            ],
        ),
        (100, "agotado", ["Cuota mensual de LOC agotada: los tickets nuevos quedan en cola."]),
    ):
        loc = await internal.registrar_loc(
            internal.LocIn(
                ticket_id=numero,
                pr_url=f"https://example.test/{tenant}/pull/{porcentaje}",
                anadidas=porcentaje - anterior,
                eliminadas=0,
                netas=porcentaje - anterior,
            ),
            svc=svc,
            idempotency_key=str(porcentaje),
        )
        assert loc.status_code == 200
        consumo = json.loads(loc.body)
        assert consumo["umbral_cruzado"] == umbral
        assert consumo["consumo_ciclo"]["porcentaje"] == porcentaje
        bytes_nuevos = mib * porcentaje // 100 - mib * anterior // 100
        if porcentaje == 100:
            with pytest.raises(AppError) as exc:
                await comercial.verificar_storage(db, tenant, bytes_nuevos)
            assert (exc.value.http, exc.value.codigo) == (402, "ALMACENAMIENTO_AGOTADO")
        else:
            for _ in range(2):
                aviso = await comercial.verificar_storage(db, tenant, bytes_nuevos)
                assert aviso == (
                    {"codigo": "ALMACENAMIENTO_AVISO", "porcentaje": 81.0}
                    if porcentaje == 81
                    else None
                )
            await db.execute(
                text("""
                    INSERT INTO core.ticket_attachments
                        (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref)
                    VALUES (:id, :t, 'evidencia.png', 'image/png', :bytes, :id)
                """),
                {"id": uuid4().hex, "t": tenant, "bytes": bytes_nuevos},
            )
        esperados.extend(nuevos)
        assert sorted(n["cuerpo"] for n in await _avisos(db, tenant)) == sorted(esperados)
        anterior = porcentaje

    respuesta = Response()
    ticket = await tickets._crear_ticket(
        db,
        UsuarioActual(tenant, tenant, "owner", False),
        respuesta,
        tipo="bug",
        titulo="Ticket en cola",
        descripcion="Cuota de LOC agotada",
    )
    assert (respuesta.status_code, ticket["etapa"]) == (202, "en_cola_por_cuota")
    assert await uno(
        db,
        "SELECT estado FROM core.jobs WHERE ticket_id = :id",
        {"id": ticket["id"]},
    ) == {"estado": "pausado_por_cuota"}
    eventos = await todos(
        db,
        """SELECT datos FROM audit.audit_events
               WHERE tenant_id = :t AND evento = 'almacenamiento_aviso_80'""",
        {"t": tenant},
    )
    assert eventos == [
        {"datos": {"usados_bytes": mib * 81 // 100, "limite_bytes": mib, "porcentaje": 81.0}}
    ]


async def test_resumen_diario_por_tenant(cuota):
    db, tenant = cuota
    numero = await _ticket(db, tenant, "en_cola_por_cuota")
    await _ticket(db, tenant)

    await avisos_cuota._avisar(tenant)
    await avisos_cuota._avisar(tenant)

    assert await _avisos(db, tenant) == [
        {"titulo": "Tickets en cola por cuota", "cuerpo": f"{numero} (3 d de antigüedad)"}
    ]


async def test_dos_renovaciones_dejan_un_job_por_ticket_desbloqueado(cuota, monkeypatch):
    db, tenant = cuota
    legado = await _ticket(db, tenant, "en_cola_por_cuota")
    monkeypatch.setattr(
        comercial,
        "estado_cuota_loc",
        AsyncMock(return_value={"umbral": "agotado", "consumidas": 100, "limite": 100}),
    )
    nuevo = await tickets._crear_ticket(
        db,
        UsuarioActual(tenant, tenant, "owner", False),
        Response(),
        tipo="bug",
        titulo="Ticket del portal",
        descripcion="Se reanuda al renovar el ciclo",
    )
    job = await uno(db, "SELECT id FROM core.jobs WHERE ticket_id = :id", {"id": nuevo["id"]})
    await db.execute(
        text("UPDATE core.subscriptions SET renueva_el = current_date WHERE tenant_id = :t"),
        {"t": tenant},
    )

    await billing_cycle._renovar(tenant)
    await billing_cycle._renovar(tenant)

    trabajos = await todos(
        db,
        """
        SELECT t.numero, t.etapa, j.id, j.estado
          FROM core.tickets t
          JOIN core.jobs j ON j.ticket_id = t.id
         WHERE t.tenant_id = :t
        """,
        {"t": tenant},
    )
    assert len(trabajos) == 2
    assert {fila["numero"] for fila in trabajos} == {legado, nuevo["numero"]}
    assert all(fila["estado"] == "pendiente_asignacion" for fila in trabajos)
    assert all(fila["etapa"] == "triage" for fila in trabajos)
    assert next(fila["id"] for fila in trabajos if fila["numero"] == nuevo["numero"]) == job["id"]
