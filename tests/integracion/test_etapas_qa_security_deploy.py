"""Test de integración KC-BE-06-2: gate1 firmado → deploy_preflight.

Requiere los fixtures `cliente_http`, `job_en_gate1_firmado`, `tenant_id` y
`ticket_numero` definidos en `tests/integracion/conftest.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.seguridad import emitir_svc_token
from kallicode_workers.orquestador.cliente_interno import ClienteInterno
from kallicode_workers.orquestador.deploy import ejecutar_deploy
from kallicode_workers.orquestador.qa import MotorPruebasNulo, ejecutar_qa
from kallicode_workers.orquestador.security import ejecutar_security

pytestmark = pytest.mark.asyncio


def _cabeceras_servicio(tenant_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant_id)}",
        "Idempotency-Key": key,
    }


async def test_flujo_completo_gate1_a_deploy_preflight(
    cliente_http: AsyncClient,
    job_en_gate1_firmado: dict[str, Any],
    tenant_id: str,
    ticket_numero: str,
) -> None:
    cliente = ClienteInterno(base_url=str(cliente_http.base_url))
    try:
        # --- 1. QA ---
        contexto_qa = job_en_gate1_firmado.setdefault("contexto", {})
        contexto_qa["qa_matrix"] = [
            {"test_id": "AC-1", "status": "pass", "raw_failure": None},
            {"test_id": "AC-2", "status": "pass", "raw_failure": None},
        ]
        contexto_qa["qa_regresion"] = {"passed": 2, "failed": 0}
        contexto_qa["qa_flaky"] = []

        resultado_qa = await ejecutar_qa(
            job_en_gate1_firmado,
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
            qa_run = await uno(
                db,
                """SELECT id, corrida FROM core.qa_runs
                   WHERE tenant_id = :t AND ticket_id = :tk
                   ORDER BY corrida DESC LIMIT 1""",
                {"t": tenant_id, "tk": job_en_gate1_firmado["ticket_id"]},
            )
            assert qa_run is not None
            assert qa_run["corrida"] == 1

        # --- 2. SECURITY ---
        contexto_sec = job_en_gate1_firmado["contexto"]
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

        resultado_sec = await ejecutar_security(
            job_en_gate1_firmado,
            cliente=cliente,
            intento=1,
        )
        assert resultado_sec is None

        # --- 3. DEPLOY_PREFLIGHT ---
        contexto_deploy = job_en_gate1_firmado["contexto"]
        contexto_deploy["destino"] = "produccion"
        contexto_deploy["deploy_resultado"] = "exito"
        contexto_deploy["security_verdict"] = {"veredicto": "approved"}
        contexto_deploy["gate_records"] = {
            "1": "aprobado",
            "2": "aprobado",
            "3": "aprobado",
        }

        resultado_deploy = await ejecutar_deploy(
            job_en_gate1_firmado,
            cliente=cliente,
            intento=1,
        )
        assert resultado_deploy is None

        respuesta_preflight = await cliente_http.post(
            f"/internal/v1/tickets/{ticket_numero}/deploy/preflight",
            json={"destino": "produccion"},
            headers=_cabeceras_servicio(tenant_id, f"verif-preflight-{ticket_numero}"),
        )
        assert respuesta_preflight.status_code == 200, respuesta_preflight.text
        preflight = respuesta_preflight.json()
        assert preflight["veredicto"] == "go", (
            f"Las 4 condiciones debían pasar; faltantes={preflight.get('faltantes')}"
        )
        assert preflight["faltantes"] == []

    finally:
        await cliente.cerrar()
