"""Pruebas de exportación asíncrona por rango, troceo 500k y filtro ticket_id (KC-BE-08-1)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import sesion_sistema
from kallicode_workers import audit_export
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


class RedisStreamDoble:
    """Envoltorio mínimo sobre RedisDoble para soportar xadd en pruebas de exportación."""

    def __init__(self, base) -> None:
        self._base = base
        self.streams: dict[str, list[dict]] = {}

    async def xadd(self, nombre: str, campos: dict, **_kwargs) -> str:
        self.streams.setdefault(nombre, []).append(campos)
        return f"{len(self.streams[nombre])}-0"

    def __getattr__(self, item: str):
        return getattr(self._base, item)


@pytest_asyncio.fixture
async def entorno_export_rango(redis_doble, monkeypatch):
    redis_stream = RedisStreamDoble(redis_doble)
    monkeypatch.setattr(comercial, "redis_cliente", lambda: redis_stream)
    monkeypatch.setattr(audit_export, "redis_cliente", lambda: redis_stream)

    from kallicode_api.routers import audit as audit_router

    monkeypatch.setattr(audit_router, "redis_cliente", lambda: redis_stream)

    tenant = f"org_exp8_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org Export KC-BE-08-1')"
            ),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Export 08', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        t1_num = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket Uno', 'Desc 1', 'bug', 'media', 'triage', :u)
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
                       VALUES (:id, :t, 'Ticket Dos', 'Desc 2', 'feature', 'alta', 'design', :u)
                       RETURNING numero"""
                ),
                {"id": str(uuid.uuid4()), "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.commit()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=owner, tenant_id=tenant, rol="owner", restringido=False
    )
    yield {"tenant": tenant, "owner": owner, "t1": t1_num, "t2": t2_num, "redis": redis_stream}
    app.dependency_overrides.clear()

    async with sesion_sistema() as db:
        for tabla in ("core.audit_exports", "core.tickets", "core.ticket_counters", "core.users"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


@pytest.fixture
def blob_memoria(monkeypatch):
    almacen: dict[str, dict] = {}

    async def subir(ref: str, contenido: bytes, content_type: str) -> str:
        almacen[ref] = {"contenido": contenido, "content_type": content_type}
        return ref

    from kallicode_api.servicios import blob as blobsvc

    monkeypatch.setattr(audit_export, "subir_archivo_servidor", subir)
    monkeypatch.setattr(blobsvc, "subir_archivo_servidor", subir)
    return almacen


@pytest.mark.integracion
async def test_ticket_id_honrado_en_integrity_y_exportacion_con_dos_tickets(
    entorno_export_rango,
    blob_memoria,
) -> None:
    """ticket_id filtra exclusivamente los eventos de ese ticket tanto en /integrity como en /exports."""
    tenant = entorno_export_rango["tenant"]
    owner = entorno_export_rango["owner"]
    t1 = entorno_export_rango["t1"]
    t2 = entorno_export_rango["t2"]

    async with sesion_sistema() as db:
        for i in range(3):
            await registrar_evento(
                db,
                tenant,
                evento="ticket_creado",
                resumen=f"Evento {i} de {t1}",
                actor_tipo="humano",
                actor_id=owner,
                etapa="triage",
                ticket_id=t1,
            )
        for i in range(5):
            await registrar_evento(
                db,
                tenant,
                evento="spec_generada",
                resumen=f"Evento {i} de {t2}",
                actor_tipo="agente",
                actor_id="agente-spec",
                etapa="design",
                ticket_id=t2,
            )
        await registrar_evento(
            db,
            tenant,
            evento="organizacion_modificada",
            resumen="Evento global sin ticket",
            actor_tipo="sistema",
        )
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. /audit/integrity honra ticket_id
        r_int_t1 = await client.get("/api/v1/audit/integrity", params={"ticket_id": t1})
        assert r_int_t1.status_code == 200
        assert r_int_t1.json() == {
            "integra": True,
            "alcance": "tramo_ticket",
            "tramo_integro": True,
            "cadena_tenant_integra": None,
            "eventos_verificados": 3,
            "primer_evento_roto": None,
        }

        r_int_t2 = await client.get("/api/v1/audit/integrity", params={"ticket_id": t2})
        assert r_int_t2.status_code == 200
        assert r_int_t2.json() == {
            "integra": True,
            "alcance": "tramo_ticket",
            "tramo_integro": True,
            "cadena_tenant_integra": None,
            "eventos_verificados": 5,
            "primer_evento_roto": None,
        }

        # Si se altera un evento de t2, t1 sigue íntegro y t2 reporta la ruptura
        async with sesion_sistema() as db:
            ev_t2 = (
                await db.execute(
                    text(
                        "SELECT id FROM audit.audit_events "
                        "WHERE tenant_id=:t AND ticket_id=:tk ORDER BY id LIMIT 1"
                    ),
                    {"t": tenant, "tk": t2},
                )
            ).scalar_one()
            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("UPDATE audit.audit_events SET resumen='MANIPULADO' WHERE id=:id"),
                {"id": ev_t2},
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            await db.commit()

        r_int_t1_post = await client.get("/api/v1/audit/integrity", params={"ticket_id": t1})
        assert r_int_t1_post.json()["integra"] is True
        assert r_int_t1_post.json()["eventos_verificados"] == 3

        r_int_t2_post = await client.get("/api/v1/audit/integrity", params={"ticket_id": t2})
        assert r_int_t2_post.json()["integra"] is False
        assert r_int_t2_post.json()["primer_evento_roto"] == ev_t2

        # 2. Exportación con ticket_id devuelve exclusivamente eventos de ese ticket
        r_exp = await client.post("/api/v1/audit/exports", json={"formato": "csv", "ticket_id": t1})
        assert r_exp.status_code == 202
        export_id = r_exp.json()["export_id"]

        await audit_export.procesar({"export_id": export_id, "tenant_id": tenant})

        r_est = await client.get(f"/api/v1/audit/exports/{export_id}")
        assert r_est.status_code == 200
        est = r_est.json()
        assert est["estado"] == "lista"
        assert est["eventos_procesados"] == 3
        assert est["partes_generadas"] == 1

        csv_bytes = blob_memoria[f"{tenant}/exports/{export_id}.csv"]["contenido"]
        filas = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
        assert len(filas) == 3
        assert {f["ticket_id"] for f in filas} == {t1}


@pytest.mark.integracion
async def test_rango_superior_a_31_dias_exige_asincrono_y_rango_menor_permite_en_linea(
    entorno_export_rango,
    blob_memoria,
) -> None:
    """Rangos > 31 días exigen camino asíncrono (202); rangos <= 31 días permiten en_linea=true."""
    tenant = entorno_export_rango["tenant"]
    t1 = entorno_export_rango["t1"]
    ahora = datetime.now(UTC)

    async with sesion_sistema() as db:
        for i in range(4):
            await registrar_evento(
                db,
                tenant,
                evento="ticket_creado",
                resumen=f"Evento reciente {i}",
                actor_tipo="sistema",
                ticket_id=t1,
            )
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # > 31 días con en_linea=True -> rechazado con 422 (exige camino asíncrono)
        r_rechazo = await client.post(
            "/api/v1/audit/exports",
            json={
                "formato": "csv",
                "en_linea": True,
                "filtros": {
                    "desde": (ahora - timedelta(days=90)).isoformat(),
                    "hasta": ahora.isoformat(),
                },
            },
        )
        assert r_rechazo.status_code == 422
        assert r_rechazo.json()["error"]["codigo"] == "RANGO_EXCESIVO"

        # <= 31 días con en_linea=True -> resuelto en línea con 200 y progreso completo
        r_linea = await client.post(
            "/api/v1/audit/exports",
            json={
                "formato": "csv",
                "ticket_id": t1,
                "en_linea": True,
                "filtros": {
                    "desde": (ahora - timedelta(days=15)).isoformat(),
                    "hasta": (ahora + timedelta(minutes=5)).isoformat(),
                },
            },
        )
        assert r_linea.status_code == 200, r_linea.text
        cuerpo_linea = r_linea.json()
        assert cuerpo_linea["estado"] == "lista"
        assert cuerpo_linea["eventos_procesados"] == 4
        assert cuerpo_linea["partes_generadas"] == 1
        assert len(cuerpo_linea["items"]) == 4


@pytest.mark.integracion
async def test_exportacion_90_dias_600k_eventos_trocea_en_dos_partes_y_manifiesto(
    entorno_export_rango,
    blob_memoria,
) -> None:
    """Exportación de 90 días con 600.000 eventos devuelve 202, trocea en 2 partes y genera manifiesto."""
    tenant = entorno_export_rango["tenant"]
    ahora = datetime.now(UTC)
    desde = ahora - timedelta(days=90)

    # Inserta 600.000 eventos sintéticos distribuidos en los últimos 90 días
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
                    'KC-' || (1 + (g % 50)),
                    'sistema',
                    'evento_sintetico',
                    'Evento sintético ' || g,
                    repeat('0', 64),
                    md5(g::text) || md5((g + 1)::text),
                    :desde + ((g::numeric / 600000.0) * interval '89 days'),
                    2
                FROM generate_series(1, 600000) AS g
                """
            ),
            {"t": tenant, "desde": desde},
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal"))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_post = await client.post(
            "/api/v1/audit/exports",
            json={
                "formato": "csv",
                "filtros": {
                    "desde": (desde - timedelta(minutes=1)).isoformat(),
                    "hasta": (ahora + timedelta(minutes=1)).isoformat(),
                },
            },
        )
        assert r_post.status_code == 202, r_post.text
        inicial = r_post.json()
        export_id = inicial["export_id"]
        assert inicial["estado"] == "encolada"
        assert inicial["eventos_procesados"] == 0
        assert inicial["partes_generadas"] == 0

        # Estado inicial antes de procesar
        r_antes = await client.get(f"/api/v1/audit/exports/{export_id}")
        assert r_antes.status_code == 200
        assert r_antes.json()["estado"] == "encolada"
        assert r_antes.json()["eventos_procesados"] == 0
        assert r_antes.json()["partes_generadas"] == 0

        # Procesar con el worker
        await audit_export.procesar({"export_id": export_id, "tenant_id": tenant})

        # Consultar estado final y progreso detallado
        r_final = await client.get(f"/api/v1/audit/exports/{export_id}")
        assert r_final.status_code == 200, r_final.text
        final = r_final.json()
        assert final["estado"] == "lista"
        assert final["eventos_procesados"] == 600_000
        assert final["partes_generadas"] == 2
        assert len(final["partes"]) == 2
        assert "download_url" in final

        # Verificar manifiesto en Blob
        manifiesto_ref = f"{tenant}/exports/{export_id}.manifest.json"
        assert final["manifiesto_ref"] == manifiesto_ref
        assert manifiesto_ref in blob_memoria
        manifiesto = json.loads(blob_memoria[manifiesto_ref]["contenido"].decode("utf-8"))
        assert manifiesto["total_eventos"] == 600_000
        assert manifiesto["eventos_procesados"] == 600_000
        assert manifiesto["partes_generadas"] == 2
        assert manifiesto["tamano_maximo_parte"] == 500_000
        assert [p["eventos"] for p in manifiesto["partes"]] == [500_000, 100_000]

        # Verificar ambas partes CSV y el recuento total exacto
        ref_p1 = f"{tenant}/exports/{export_id}.csv"
        ref_p2 = f"{tenant}/exports/{export_id}.part-0002.csv"
        assert ref_p1 in blob_memoria
        assert ref_p2 in blob_memoria

        filas_p1 = list(
            csv.DictReader(io.StringIO(blob_memoria[ref_p1]["contenido"].decode("utf-8")))
        )
        filas_p2 = list(
            csv.DictReader(io.StringIO(blob_memoria[ref_p2]["contenido"].decode("utf-8")))
        )
        assert len(filas_p1) == 500_000
        assert len(filas_p2) == 100_000
        assert len(filas_p1) + len(filas_p2) == 600_000
