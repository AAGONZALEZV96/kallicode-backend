"""Pruebas de minimización, allowlist y límite duro de payloads de auditoría (KC-BE-08-3 / BE-F31)."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main import app
from kallicode_core.auditoria import (
    ALLOWLIST_DATOS_EVENTO,
    EVENTOS,
    MAX_BYTES_DATOS_AUDITORIA,
    MAX_TEXTO_AUDITORIA,
    depurar_datos_evento,
    registrar_evento,
    verificar_cadena,
)
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.errors import AppError
from sqlalchemy import text


def test_allowlist_cubre_todos_los_eventos_y_recorta_claves_no_permitidas() -> None:
    """Cada evento del catálogo tiene allowlist declarada y descarta claves fuera de ella."""
    for codigo in EVENTOS:
        assert codigo in ALLOWLIST_DATOS_EVENTO, f"Falta allowlist para evento '{codigo}'"

    depurado = depurar_datos_evento(
        "escalado_a_humano",
        {
            "motivo": "validacion_persistente",
            "severidad": "alta",
            "resumen_detalle": "x" * 2_000,
            "codigo_cliente_privado": "SECRET_SOURCE_CODE",
            "email_reporter": "usuario@cliente.com",
        },
        blob_ref="org_test/audit-payloads/esc-1.json",
        blob_sha256="a" * 64,
    )
    assert depurado is not None
    assert set(depurado.keys()) <= set(ALLOWLIST_DATOS_EVENTO["escalado_a_humano"])
    assert "codigo_cliente_privado" not in depurado
    assert "email_reporter" not in depurado
    assert len(depurado["resumen_detalle"]) == MAX_TEXTO_AUDITORIA + len("…[truncado]")
    assert depurado["blob_ref"] == "org_test/audit-payloads/esc-1.json"


def test_depurar_datos_lanza_error_explicito_si_supera_limite_duro() -> None:
    """Si un payload permitido excede MAX_BYTES_DATOS_AUDITORIA, falla antes del INSERT."""
    criterios_gigantes = [f"CRIT-{i:04d}-" + ("X" * 400) for i in range(40)]
    with pytest.raises(AppError) as exc_info:
        depurar_datos_evento("qa_escalado", {"criterios": criterios_gigantes})
    assert exc_info.value.codigo == "PAYLOAD_AUDITORIA_EXCEDIDO"
    assert (
        len(json.dumps({"criterios": criterios_gigantes}).encode("utf-8"))
        > MAX_BYTES_DATOS_AUDITORIA
    )


@pytest_asyncio.fixture
async def entorno_minimizacion(monkeypatch):
    almacen_blob: dict[str, dict] = {}

    async def subir_falso(
        ref: str, contenido: bytes, content_type: str = "application/json"
    ) -> str:
        almacen_blob[ref] = {"contenido": contenido, "content_type": content_type}
        return ref

    from kallicode_api.routers import internal as internal_router

    monkeypatch.setattr(internal_router, "subir_archivo_servidor", subir_falso)

    tenant = f"org_min83_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = str(uuid.uuid4())
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org Min KC-BE-08-3')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Min 083', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        numero = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket Min', 'Desc', 'bug', 'alta', 'build', :u)
                       RETURNING numero"""
                ),
                {"id": ticket_id, "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado)
                   VALUES (:j, :t, :tk, 'build')"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )
        await db.commit()

    app.dependency_overrides[servicio_actual] = lambda: ServicioActual(
        servicio="agente-build", tenant_id=tenant
    )
    yield {
        "tenant": tenant,
        "numero": numero,
        "job_id": job_id,
        "blob": almacen_blob,
    }
    app.dependency_overrides.clear()

    async with sesion_sistema() as db:
        for tabla in (
            "core.escalations",
            "core.jobs",
            "core.tickets",
            "core.ticket_counters",
            "core.users",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


@pytest.mark.integracion
async def test_escalacion_con_clave_no_permitida_y_texto_100kb_se_depura_y_mantiene_cadena(
    entorno_minimizacion,
) -> None:
    """Envía detalle de escalación con clave prohibida y 100 KB; persiste depurado bajo límite y cadena íntegra."""
    tenant = entorno_minimizacion["tenant"]
    numero = entorno_minimizacion["numero"]
    job_id = entorno_minimizacion["job_id"]
    blob = entorno_minimizacion["blob"]

    texto_100kb = "EXTRACCION_CODIGO_CLIENTE_" + ("A" * (100 * 1024))
    detalle_entrada = {
        "clave_no_permitida": "pii-sensible@cliente.cl",
        "resumen_detalle": texto_100kb,
        "criterios": ["CRIT-01"],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/internal/v1/escalations",
            headers={"Idempotency-Key": f"idem-esc-{uuid.uuid4().hex}"},
            json={
                "job_id": job_id,
                "etapa": "build",
                "paso": "generar_patch",
                "motivo": "ambiguedad_critica",
                "severidad": "alta",
                "detalle": detalle_entrada,
            },
        )
        assert resp.status_code == 201, resp.text
        escalation_id = resp.json()["escalation_id"]

    async with sesion_sistema() as db:
        ev = await uno(
            db,
            """
            SELECT id, datos, pg_column_size(datos) AS bytes_col
              FROM audit.audit_events
             WHERE tenant_id = :t AND ticket_id = :tk AND evento = 'escalado_a_humano'
             ORDER BY id DESC LIMIT 1
            """,
            {"t": tenant, "tk": numero},
        )
        assert ev is not None
        datos_persistidos = ev["datos"]
        assert isinstance(datos_persistidos, dict)

        # 1. Clave no permitida eliminada y sólo claves de allowlist presentes
        assert "clave_no_permitida" not in datos_persistidos
        assert set(datos_persistidos.keys()) <= set(ALLOWLIST_DATOS_EVENTO["escalado_a_humano"])

        # 2. Texto de 100 KB truncado y tamaño total por debajo del límite duro
        assert len(datos_persistidos["resumen_detalle"]) == MAX_TEXTO_AUDITORIA + len("…[truncado]")
        tamano_serializado = len(json.dumps(datos_persistidos, ensure_ascii=False).encode("utf-8"))
        assert tamano_serializado < MAX_BYTES_DATOS_AUDITORIA

        # 3. Contenido voluminoso volcado a Blob y referenciado en el evento
        blob_ref = datos_persistidos["blob_ref"]
        assert blob_ref.startswith(f"{tenant}/audit-payloads/escalacion-{escalation_id}-")
        assert blob_ref in blob
        assert len(blob[blob_ref]["contenido"]) > 100 * 1024

        # 4. La cadena de hashes sigue verificando íntegra
        verif = await verificar_cadena(db, tenant)
        assert verif["integra"] is True

        # 5. Límite duro antes del INSERT rechaza explícitamente payloads desmesurados sin escribir fila
        conteo_antes = (
            await uno(
                db,
                "SELECT count(*) AS n FROM audit.audit_events WHERE tenant_id = :t",
                {"t": tenant},
            )
        )["n"]
        with pytest.raises(AppError) as exc_info:
            await registrar_evento(
                db,
                tenant,
                evento="qa_escalado",
                resumen="Intento desmesurado",
                actor_tipo="agente",
                actor_id="qa",
                ticket_id=numero,
                datos={"criterios": [f"C-{i}-" + ("Z" * 400) for i in range(40)]},
            )
        assert exc_info.value.codigo == "PAYLOAD_AUDITORIA_EXCEDIDO"
        conteo_despues = (
            await uno(
                db,
                "SELECT count(*) AS n FROM audit.audit_events WHERE tenant_id = :t",
                {"t": tenant},
            )
        )["n"]
        assert conteo_despues == conteo_antes
