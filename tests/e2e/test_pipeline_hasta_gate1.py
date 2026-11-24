"""Test E2E del pipeline hasta el Gate 1 firmado (KC-BE-06-6)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import Counter
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text

from .conftest import SECRETO_WEBHOOK

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


@pytest.mark.parametrize("con_job", [True, False], ids=["nuevo", "anterior"])
async def test_cancelar_ticket_portal(
    client: AsyncClient, seed_tenant_y_conexion, monkeypatch, con_job: bool
) -> None:
    from kallicode_api.routers import tickets

    tenant_id = seed_tenant_y_conexion["tenant_id"]
    user_id = seed_tenant_y_conexion["user_id"]
    headers = {"Authorization": f"Bearer {emitir_access_token(user_id, tenant_id, 'approver')}"}
    monkeypatch.setattr(tickets, "_embeber_y_persistir", AsyncMock(return_value=None))
    creado = await client.post(
        "/api/v1/tickets",
        json={"tipo": "bug", "titulo": "Ticket para cancelar", "descripcion": "Prueba de cierre."},
        headers=headers,
    )
    assert creado.status_code == 201, creado.text
    params = {"t": tenant_id, "n": creado.json()["id"]}
    if not con_job:
        async with sesion_sistema() as db:
            await db.execute(
                text("""
                    DELETE FROM core.jobs j USING core.tickets t
                     WHERE j.ticket_id = t.id AND t.tenant_id = :t AND t.numero = :n
                """),
                params,
            )

    cancelado = await client.patch(
        f"/api/v1/tickets/{params['n']}",
        json={"accion": "cancelar", "motivo_cancelacion": "Se cancela la prueba"},
        headers=headers,
    )
    assert cancelado.status_code == 200, cancelado.text
    assert cancelado.json()["etapa"] == "cancelado"
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT t.etapa, t.cerrado_en, t.cancelado_por, j.estado AS estado_job
              FROM core.tickets t LEFT JOIN core.jobs j ON j.ticket_id = t.id
             WHERE t.tenant_id = :t AND t.numero = :n
            """,
            params,
        )
    assert fila["etapa"] == "cancelado"
    assert fila["cerrado_en"] is not None
    assert fila["cancelado_por"] == user_id
    assert fila["estado_job"] == ("cancelado" if con_job else None)


def _firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO_WEBHOOK.encode(), cuerpo, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("origen", ["webhook", "portal", "funcionalidad"])
async def test_pipeline_hasta_gate1(
    client: AsyncClient,
    cliente_orquestador,
    seed_tenant_y_conexion,
    servicios_grabados,
    monkeypatch,
    origen,
):
    datos = seed_tenant_y_conexion
    tenant_id = datos["tenant_id"]
    user_id = datos["user_id"]
    titulo = "Login fails with valid credentials"
    descripcion = "When I log in with correct credentials, the form returns 500."
    if origen == "webhook":
        ruta_token = datos["ruta_token"]

        # --- 1. Webhook firmado ---
        payload = {
            "action": "opened",
            "issue": {
                "number": 42,
                "title": titulo,
                "body": descripcion,
                "labels": [{"name": "bug"}],
            },
        }
        cuerpo = json.dumps(payload).encode()
        r = await client.post(
            f"/api/v1/webhooks/github/{ruta_token}",
            content=cuerpo,
            headers={
                "X-Hub-Signature-256": _firmar(cuerpo),
                "X-Webhook-Timestamp": str(int(time.time())),
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": "del-e2e-001",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 202, r.text

        # --- 2. Normalizer ---
        from kallicode_workers.normalizer import procesar_inbox

        async with sesion_sistema() as db:
            inbox = await uno(
                db,
                "SELECT id FROM core.webhook_inbox WHERE tenant_id=:t ORDER BY id DESC LIMIT 1",
                {"t": tenant_id},
            )
        await procesar_inbox({"inbox_id": inbox["id"]})

        async with sesion_sistema() as db:
            inbox = await uno(
                db,
                "SELECT estado, ticket_id FROM core.webhook_inbox WHERE id=:i",
                {"i": inbox["id"]},
            )
            assert inbox["estado"] == "procesado"
            jobs = await todos(
                db, "SELECT * FROM core.jobs WHERE ticket_id=:tk", {"tk": inbox["ticket_id"]}
            )
    else:
        from kallicode_api.routers import tickets

        monkeypatch.setattr(tickets, "_embeber_y_persistir", AsyncMock(return_value=None))
        headers = {"Authorization": f"Bearer {emitir_access_token(user_id, tenant_id, 'approver')}"}
        if origen == "portal":
            r = await client.post(
                "/api/v1/tickets",
                json={
                    "tipo": "bug",
                    "titulo": titulo,
                    "descripcion": descripcion,
                    "prioridad": "alta",
                },
                headers=headers,
            )
        else:
            r = await client.post(
                "/api/v1/tickets/features",
                json={"nombre": titulo, "objetivo": descripcion},
                headers=headers,
            )
        assert r.status_code == 201, r.text
        assert r.json()["etapa"] == "triage"
        async with sesion_sistema() as db:
            jobs = await todos(
                db,
                """
                SELECT j.*
                  FROM core.jobs j
                  JOIN core.tickets t ON t.id = j.ticket_id
                 WHERE t.tenant_id = :t AND t.numero = :n
                """,
                {"t": tenant_id, "n": r.json()["id"]},
            )
            await db.execute(
                text("INSERT INTO core.production_lines (tenant_id, numero) VALUES (:t, 1)"),
                {"t": tenant_id},
            )
            await db.execute(
                text("UPDATE core.organizations SET fabrica_activa=false WHERE id=:t"),
                {"t": tenant_id},
            )

    assert len(jobs) == 1
    job = jobs[0]
    if origen != "webhook":
        assert job["prioridad"] == ("P1" if origen == "portal" else "P2")
        assert job["contexto"] == {"titulo": titulo, "descripcion": descripcion, "origen": "portal"}
    job_id = job["id"]
    assert job["estado"] == "pendiente_asignacion"

    from kallicode_workers.orquestador.bucle import procesar_job

    esperados: dict[str, str] = {}

    async def comprobar(estado):
        async with sesion_sistema() as db:
            actual = await uno(db, "SELECT * FROM core.jobs WHERE id=:j", {"j": job_id})
            pasos = await todos(db, "SELECT * FROM core.llm_steps WHERE job_id=:j", {"j": job_id})
            usos = await todos(
                db, "SELECT * FROM core.usage_tokens WHERE ticket_id=:tk", {"tk": job["ticket_id"]}
            )
            assert (await verificar_cadena(db, tenant_id))["integra"] is True
        assert actual["estado"] == estado, actual
        assert len(pasos) == len(esperados)
        assert {p["paso"]: p["tier"] for p in pasos} == esperados
        campos = ("etapa", "modelo", "tier", "tokens_in", "tokens_out", "duracion_ms")
        assert Counter(tuple(u[c] for c in campos) for u in usos) == Counter(
            tuple(p[c] for c in campos) for p in pasos
        )
        assert all(p["tokens_in"] == 64 and p["tokens_out"] == 32 for p in pasos)
        assert all(p["validacion"] == "esquema_valido" and p["reintentos"] == 0 for p in pasos)
        return actual

    async def ejecutar(estado):
        # El alta del portal se prueba con la asignación real a una línea.
        if estado == "triage" and origen != "webhook":
            from kallicode_workers.scheduler import _asignar_tenant

            await _asignar_tenant(tenant_id)
            async with sesion_sistema() as db:
                assert await uno(
                    db,
                    "SELECT estado, linea FROM core.jobs WHERE id=:j",
                    {"j": job_id},
                ) == {"estado": "pendiente_asignacion", "linea": None}
                await db.execute(
                    text("UPDATE core.organizations SET fabrica_activa=true WHERE id=:t"),
                    {"t": tenant_id},
                )
            await _asignar_tenant(tenant_id)
        async with sesion_sistema() as db:
            if estado == "triage" and origen == "webhook":
                await db.execute(
                    text(
                        "UPDATE core.jobs SET estado='triage' WHERE id=:j AND estado='pendiente_asignacion'"
                    ),
                    {"j": job_id},
                )
            reservado = await uno(db, "SELECT * FROM core.jobs WHERE id=:j", {"j": job_id})
        assert reservado["estado"] == estado
        if estado == "triage" and origen != "webhook":
            assert reservado["linea"] == 1
        await procesar_job(reservado, cliente_orquestador)

    await comprobar("pendiente_asignacion")
    await ejecutar("triage")
    esperados.update(
        {"triage.classify": "flash", "triage.dedup": "flash", "triage.impact": "flash"}
    )
    await comprobar("design")
    await ejecutar("design")
    esperados.update(
        {
            "design.digest": "flash",
            "design.alternatives": "pro",
            "design.adversarial_review": "fable",
            "design.compile": "flash",
        }
    )
    await comprobar("gate1")
    # El orquestador no ejecuta build mientras espera la firma humana.
    await ejecutar("gate1")
    await comprobar("gate1")

    # spec con alternativas y criterios
    async with sesion_sistema() as db:
        spec = await uno(
            db,
            "SELECT * FROM core.specs WHERE tenant_id=:t ORDER BY version DESC LIMIT 1",
            {"t": tenant_id},
        )
        alts = await todos(
            db,
            "SELECT alt_id, titulo, recomendada FROM core.spec_alternatives WHERE spec_id=:s",
            {"s": spec["id"]},
        )
        crits = await todos(
            db,
            "SELECT criterio_id, given_txt, when_txt, then_txt FROM core.spec_criteria WHERE spec_id=:s",
            {"s": spec["id"]},
        )
    assert spec["estado"] == "pendiente_gate1"
    assert {a["alt_id"] for a in alts} == {"A", "B"}
    assert next(a for a in alts if a["recomendada"])["alt_id"] == "A"
    assert {c["criterio_id"] for c in crits} == {"AC-1", "AC-2"}
    assert all(c["given_txt"] and c["when_txt"] and c["then_txt"] for c in crits)
    assert spec["scope_paths"] == ["src/auth.py"]
    assert servicios_grabados.artefactos[spec["spec_md_ref"]].decode().startswith("# Fix login")

    # --- 4. Firma del Gate 1 ---
    async with sesion_sistema() as db:
        ticket_row = await uno(
            db,
            "SELECT numero FROM core.tickets WHERE tenant_id=:t ORDER BY creado_en DESC LIMIT 1",
            {"t": tenant_id},
        )
    jwt_aprobador = emitir_access_token(user_id, tenant_id, "approver")
    r = await client.post(
        f"/api/v1/tickets/{ticket_row['numero']}/gates/1/approve",
        json={"comentario": "Aprobado en E2E", "alternativa_id": "A"},
        headers={"Authorization": f"Bearer {jwt_aprobador}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "firmado"

    # gate_signatures
    async with sesion_sistema() as db:
        firma = await uno(
            db,
            "SELECT gate, accion, actor_id, sello, spec_id, alternativa_id FROM core.gate_signatures WHERE tenant_id=:t AND gate=1",
            {"t": tenant_id},
        )
    assert firma["accion"] == "aprobado"
    assert firma["actor_id"] == user_id
    assert firma["spec_id"] == spec["id"]
    assert firma["alternativa_id"] == "A"
    async with sesion_sistema() as db:
        aprobada = await uno(db, "SELECT estado FROM core.specs WHERE id=:s", {"s": spec["id"]})
        elegidas = await todos(
            db,
            "SELECT alt_id FROM core.spec_alternatives WHERE spec_id=:s AND elegida",
            {"s": spec["id"]},
        )
    assert aprobada["estado"] == "aprobada"
    assert elegidas == [{"alt_id": "A"}]

    # Build se habilita exclusivamente después de firmar Gate 1.
    await comprobar("build")
    async with sesion_sistema() as db:
        await db.execute(
            text("UPDATE core.jobs SET contexto = contexto || CAST(:ctx AS jsonb) WHERE id=:j"),
            {
                "j": job_id,
                "ctx": json.dumps(
                    {"archivos": {"src/auth.py": "def login(user, pwd):\n    return True\n"}}
                ),
            },
        )
    await ejecutar("build")
    esperados.update({"build.plan": "pro", "build.diff.1": "flash", "build.selfreview": "fable"})
    await comprobar("qa")
    assert servicios_grabados.llamadas == [
        (p.replace("build.diff.1", "build.diff"), t) for p, t in esperados.items()
    ]
    async with sesion_sistema() as db:
        eventos = await todos(
            db,
            "SELECT * FROM audit.audit_events WHERE tenant_id=:t AND ticket_id=:tk ORDER BY creado_en, id",
            {"t": tenant_id, "tk": ticket_row["numero"]},
        )
    assert {e["evento"] for e in eventos} >= {
        "ticket_creado",
        "paso_llm",
        "spec_generada",
        "gate_firmado",
    }
    evento_firma = next(e for e in eventos if e["evento"] == "gate_firmado")
    assert evento_firma["sello"] == firma["sello"]
    assert evento_firma["actor_id"] == user_id
