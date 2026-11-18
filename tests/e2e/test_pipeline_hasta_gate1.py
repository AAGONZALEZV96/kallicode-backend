"""Test E2E del pipeline hasta el Gate 1 firmado (KC-BE-06-6)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import Counter

import pytest
from httpx import AsyncClient
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text

from .conftest import SECRETO_WEBHOOK

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


def _firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO_WEBHOOK.encode(), cuerpo, hashlib.sha256).hexdigest()


async def test_pipeline_hasta_gate1(
    client: AsyncClient,
    cliente_orquestador,
    seed_tenant_y_conexion,
    servicios_grabados,
):
    datos = seed_tenant_y_conexion
    tenant_id = datos["tenant_id"]
    user_id = datos["user_id"]
    ruta_token = datos["ruta_token"]

    # --- 1. Webhook firmado ---
    payload = {
        "action": "opened",
        "issue": {
            "number": 42,
            "title": "Login fails with valid credentials",
            "body": "When I log in with correct credentials, the form returns 500.",
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
            db, "SELECT estado, ticket_id FROM core.webhook_inbox WHERE id=:i", {"i": inbox["id"]}
        )
        assert inbox["estado"] == "procesado"
        job = await uno(
            db, "SELECT * FROM core.jobs WHERE ticket_id=:tk", {"tk": inbox["ticket_id"]}
        )
    assert job is not None, "El normalizador debe crear el job"
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
        # La asignación inicial pertenece al scheduler; las etapas y sus transiciones
        # se ejecutan con el orquestador y las APIs reales, limitadas a este job.
        async with sesion_sistema() as db:
            if estado == "triage":
                await db.execute(
                    text(
                        "UPDATE core.jobs SET estado='triage' WHERE id=:j AND estado='pendiente_asignacion'"
                    ),
                    {"j": job_id},
                )
            reservado = await uno(db, "SELECT * FROM core.jobs WHERE id=:j", {"j": job_id})
        assert reservado["estado"] == estado
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
