"""Test de integración KC-BE-06-2: gate1 firmado → deploy_preflight."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from kallicode_core import comercial
from kallicode_core.db import sesion_sistema
from kallicode_core.redis_keys import clave_tenant
from kallicode_core.seguridad import emitir_svc_token
from kallicode_workers.orquestador.cliente_interno import ClienteInterno
from kallicode_workers.orquestador.deploy import ejecutar_deploy
from kallicode_workers.orquestador.qa import MotorPruebasNulo, ejecutar_qa
from kallicode_workers.orquestador.security import ejecutar_security
from sqlalchemy import text

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skip(reason="Pendiente de KC-QA-06-2: mock del LLM y seed de Testing"),
]


def _cabeceras_servicio(tenant_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant_id)}",
        "Idempotency-Key": key,
    }


async def _sembrar_job_en_security(
    db_admin: Any,
    tenant_id: str,
    ticket: dict[str, Any],
) -> dict[str, Any]:
    """Crea job, spec, criterios, usuario firmante y firmas gate1/2/3."""
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    spec_id = f"sp_{uuid.uuid4().hex[:12]}"
    user_id = f"u_firmante_{uuid.uuid4().hex[:8]}"

    async with sesion_sistema() as db:
        # 1. Usuario firmante
        await db.execute(
            text(
                """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                   VALUES (:id, :t, :email, 'Firmante Test', 'admin')"""
            ),
            {
                "id": user_id,
                "t": tenant_id,
                "email": f"firmante-{user_id[:8]}@{tenant_id[:8]}.test",
            },
        )

        # 2. Job en estado security
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:id, :t, :tk, 'security', 'P3')"""
            ),
            {"id": job_id, "t": tenant_id, "tk": ticket["id"]},
        )

        # 3. Spec aprobada
        await db.execute(
            text(
                """INSERT INTO core.specs
                   (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths,
                    migraciones, estado)
                   VALUES (:id, :t, :tk, 1, 'mem://spec.md', :sp,
                           CAST(:mg AS jsonb), 'aprobada')"""
            ),
            {
                "id": spec_id,
                "t": tenant_id,
                "tk": ticket["id"],
                "sp": ["src/"],
                "mg": '[{"archivo":"001_x.sql","descripcion":"x","rollback":"DROP TABLE x"}]',
            },
        )

        # 4. Criterios de aceptación
        for criterio in ("AC-1", "AC-2"):
            await db.execute(
                text(
                    """INSERT INTO core.spec_criteria
                       (spec_id, tenant_id, criterio_id, given_txt, when_txt, then_txt)
                       VALUES (:s, :t, :c, 'g', 'w', 't')"""
                ),
                {"s": spec_id, "t": tenant_id, "c": criterio},
            )

        # 5. Firmas gate1, gate2, gate3 (con sello incluido)
        for gate in (1, 2, 3):
            await db.execute(
                text(
                    """INSERT INTO core.gate_signatures
                       (id, tenant_id, ticket_id, spec_id, gate, accion,
                        actor_id, acepta_fallos, sello)
                       VALUES (:id, :t, :tk, :sp, :g, 'aprobado',
                               :uid, false, :sello)"""
                ),
                {
                    "id": f"gs_{gate}_{uuid.uuid4().hex[:8]}",
                    "t": tenant_id,
                    "tk": ticket["id"],
                    "sp": spec_id,
                    "g": gate,
                    "uid": user_id,
                    "sello": f"sello_test_{gate}_{uuid.uuid4().hex}",
                },
            )

        await db.commit()

    await comercial.redis_cliente().set(clave_tenant(tenant_id, "pipeline", "disponible"), "1")

    return {
        "id": job_id,
        "tenant_id": tenant_id,
        "ticket_id": ticket["id"],
        "ticket_numero": ticket["numero"],
        "estado": "security",
        "contexto": {
            "destino": "produccion",
            "gate_records": {"1": "aprobado", "2": "aprobado", "3": "aprobado"},
            "security_verdict": {"veredicto": "approved"},
            "deploy_resultado": "exito",
        },
    }


async def test_flujo_completo_gate1_a_deploy_preflight(
    client: AsyncClient,
    seed_dos_tenants,
    crear_ticket,
    db_admin,
) -> None:
    tenant_a, _tenant_b = seed_dos_tenants
    ticket = await crear_ticket(tenant_id=tenant_a, etapa="security")

    async with sesion_sistema() as db:
        fila = await db.execute(
            text("SELECT id, numero FROM core.tickets WHERE numero = :n AND tenant_id = :t"),
            {"n": ticket["numero"], "t": tenant_a},
        )
        ticket = dict(fila.mappings().one())

    job = await _sembrar_job_en_security(db_admin, tenant_a, ticket)

    cliente = ClienteInterno(base_url=str(client.base_url))
    try:
        # --- 1. QA ---
        contexto_qa = job["contexto"]
        contexto_qa["qa_matrix"] = [
            {"test_id": "AC-1", "status": "pass", "raw_failure": None},
            {"test_id": "AC-2", "status": "pass", "raw_failure": None},
        ]
        contexto_qa["qa_regresion"] = {"passed": 2, "failed": 0}
        contexto_qa["qa_flaky"] = []

        resultado_qa = await ejecutar_qa(
            job,
            cliente=cliente,
            intento=1,
            motor=MotorPruebasNulo(
                matriz=contexto_qa["qa_matrix"],
                regresion=contexto_qa["qa_regresion"],
                flaky=[],
            ),
        )
        assert resultado_qa is None

        async with sesion_sistema() as db:
            fila = await db.execute(
                text(
                    """SELECT id, corrida FROM core.qa_runs
                       WHERE tenant_id = :t AND ticket_id = :tk
                       ORDER BY corrida DESC LIMIT 1"""
                ),
                {"t": tenant_a, "tk": ticket["id"]},
            )
            qa_run = fila.mappings().one_or_none()
            assert qa_run is not None
            assert qa_run["corrida"] == 1

        # --- 2. SECURITY ---
        contexto_sec = job["contexto"]
        contexto_sec["sast"] = {
            "results": [
                {
                    "check_id": "python.lang.security.audit.eval",
                    "path": "src/x.py",
                    "start": {"line": 10},
                    "extra": {"severity": "ERROR"},
                }
            ]
        }
        contexto_sec["sca"] = {"Results": []}
        contexto_sec["secrets"] = {"findings": []}

        resultado_sec = await ejecutar_security(job, cliente=cliente, intento=1)
        assert resultado_sec is None

        # --- 3. DEPLOY_PREFLIGHT ---
        resultado_deploy = await ejecutar_deploy(job, cliente=cliente, intento=1)
        assert resultado_deploy is None

        respuesta_preflight = await client.post(
            f"/internal/v1/tickets/{ticket['numero']}/deploy/preflight",
            json={"destino": "produccion"},
            headers=_cabeceras_servicio(tenant_a, f"verif-preflight-{ticket['numero']}"),
        )
        assert respuesta_preflight.status_code == 200, respuesta_preflight.text
        preflight = respuesta_preflight.json()
        assert preflight["veredicto"] == "go", (
            f"Las 4 condiciones debían pasar; faltantes={preflight.get('faltantes')}"
        )
        assert preflight["faltantes"] == []
    finally:
        await cliente.cerrar()
