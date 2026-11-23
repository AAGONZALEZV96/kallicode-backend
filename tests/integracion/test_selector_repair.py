"""Pruebas conjuntas de escalación de reparación de selectores con PR (KC-BE-09-5 / KC-QA-09-3).

Verifica:
1. Validación tipada del detalle (`SelectorRepairDetalleIn`) con selector antiguo,
   selector propuesto, confianza, prueba afectada y referencia al pull request.
2. La escalación con `motivo="selector_repair"` NO detiene el job del ticket de
   producto (no pasa a `escalado_humano`) y queda encolada como trabajo de
   mantenimiento en su propia cola Redis (`kc:selector_repair`).
3. Flujo conjunto con `SelectorRepairEngine` y `GitFixCommitter` de Testing por
   encima (>= 0.8) y por debajo (< 0.8) del umbral de confianza.
4. Visibilidad de la reparación con su confianza y PR en el expediente del ticket
   (`GET /api/v1/audit/tickets/{numero}`) y en su pestaña de actividad
   (`GET /api/v1/tickets/{numero}/activity`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from kallicode_api.esquemas.interna import STREAM_SELECTOR_REPAIR
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_access_token, emitir_svc_token
from kallicode_testing.git_fix_committer import GitFixCommitter
from kallicode_testing.selector_repair import (
    RepairRequestDTO,
    SelectorCandidateDTO,
    SelectorRepairEngine,
)
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _cabeceras_svc(tenant_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant_id)}",
        "Idempotency-Key": key,
    }


def _cabeceras_portal(user_id: str, tenant_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_access_token(user_id, tenant_id, 'owner')}",
    }


async def _preparar_escenario(tenant_id: str) -> dict[str, Any]:
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = str(uuid.uuid4())
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                   VALUES (:id, :t, :email, 'Owner QA', 'owner')"""
            ),
            {"id": user_id, "t": tenant_id, "email": f"{user_id}@{tenant_id[:8]}.test"},
        )
        fila_tk = await db.execute(
            text(
                """INSERT INTO core.tickets
                   (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en)
                   VALUES (:id, :t, 'Flujo checkout e2e', 'Validar botón pagar',
                           'bug', 'media', 'qa', now())
                   RETURNING id, numero"""
            ),
            {"id": ticket_id, "t": tenant_id},
        )
        ticket = dict(fila_tk.mappings().one())
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:id, :t, :tk, 'qa', 'P2')"""
            ),
            {"id": job_id, "t": tenant_id, "tk": ticket["id"]},
        )
        await db.commit()

    return {
        "user_id": user_id,
        "ticket_id": ticket["id"],
        "numero": ticket["numero"],
        "job_id": job_id,
    }


async def test_selector_repair_valida_contrato_detalle(
    client_interna: AsyncClient,
    seed_dos_tenants,
) -> None:
    """Rechaza con 422 detalles incompletos o sin PR cuando confianza >= 0.8."""
    tenant_a, _ = seed_dos_tenants
    esc = await _preparar_escenario(tenant_a)

    # Falta prueba_afectada y pr_ref con confianza >= 0.8
    resp = await client_interna.post(
        "/internal/v1/escalations",
        headers=_cabeceras_svc(tenant_a, f"sr-invalid-{uuid.uuid4().hex}"),
        json={
            "job_id": esc["job_id"],
            "etapa": "qa",
            "paso": "qa.e2e.AC-1",
            "motivo": "selector_repair",
            "severidad": "baja",
            "detalle": {
                "selector_antiguo": "#btn-old",
                "selector_propuesto": "[data-testid='btn-pay']",
                "confianza": 0.92,
            },
        },
    )
    assert resp.status_code == 422, resp.text


async def test_selector_repair_sobre_umbral_con_pr_no_detiene_job_y_refleja_en_portal(
    client_interna: AsyncClient,
    client: AsyncClient,
    seed_dos_tenants,
    tmp_path: Path,
) -> None:
    """Con confianza >= 0.8 Testing genera PR, se encola en mantenimiento y se ve en expediente y actividad."""
    tenant_a, _ = seed_dos_tenants
    esc = await _preparar_escenario(tenant_a)

    # 1. Ejecutar motor real de Testing por encima del umbral 0.8
    spec_rel = "e2e/tests/checkout.spec.ts"
    spec_file = tmp_path / spec_rel
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "test('pagar', async ({ page }) => {\n"
        "  await page.locator('#btn-pagar-old').click();\n"
        "});\n",
        encoding="utf-8",
    )

    committer = GitFixCommitter(
        repo_path=tmp_path,
        command_executor=lambda args, _cwd=None: (
            (0, "deadbeef1234567890abcdef1234567890abcdef", "")
            if args[:2] == ["rev-parse", "HEAD"]
            else (0, "ok", "")
        ),
    )
    engine = SelectorRepairEngine(
        command_executor=lambda _cmd: True,
        confidence_floor=0.8,
        git_committer=committer,
    )
    resultado = engine.repair_selector(
        RepairRequestDTO(
            spec_path=spec_rel,
            line_number=2,
            broken_selector="#btn-pagar-old",
            step_intent="Clic en botón confirmar pago",
            dom_excerpt='<button data-testid="checkout.pay.btn-submit">Pagar</button>',
        ),
        base_dir=tmp_path,
    )
    assert resultado.status == "patched"
    assert resultado.applied_candidate is not None
    assert resultado.applied_candidate.confidence >= 0.8
    assert resultado.branch_name is not None

    pr_mock = engine.commit_and_mock_pr(
        criterion_id="AC-1",
        branch_name=resultado.branch_name,
        changed_files=[spec_rel],
    )
    assert pr_mock is not None

    # 2. Emitir escalación selector_repair al backend
    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock(return_value="1700000000000-0")

    with patch("kallicode_api.routers.internal.redis_cliente", return_value=mock_redis):
        resp = await client_interna.post(
            "/internal/v1/escalations",
            headers=_cabeceras_svc(tenant_a, f"sr-high-{uuid.uuid4().hex}"),
            json={
                "job_id": esc["job_id"],
                "etapa": "qa",
                "paso": "qa.e2e.AC-1",
                "motivo": "selector_repair",
                "severidad": "baja",
                "detalle": {
                    "selector_antiguo": "#btn-pagar-old",
                    "selector_propuesto": resultado.applied_candidate.selector,
                    "confianza": resultado.applied_candidate.confidence,
                    "prueba_afectada": spec_rel,
                    "pr_ref": pr_mock.pr_id,
                    "pr_id": pr_mock.pr_id,
                    "branch_name": pr_mock.branch_name,
                    "commit_sha": pr_mock.commit_sha,
                },
            },
        )

    assert resp.status_code in (200, 201), resp.text
    cuerpo = resp.json()
    assert cuerpo["estado"] == "abierta"
    assert cuerpo["cola"] == STREAM_SELECTOR_REPAIR
    assert cuerpo["job_detenido"] is False
    assert cuerpo["supera_umbral"] is True

    # 3. Verificar que el job de producto NO quedó detenido en escalado_humano
    async with sesion_sistema() as db:
        estado_job = (
            await db.execute(
                text("SELECT estado FROM core.jobs WHERE id=:j"),
                {"j": esc["job_id"]},
            )
        ).scalar()
        assert estado_job == "qa"

    # 4. Verificar encolamiento en stream dedicado de mantenimiento
    mock_redis.xadd.assert_awaited_once()
    stream_usado, payload_cola = mock_redis.xadd.await_args.args[:2]
    assert stream_usado == STREAM_SELECTOR_REPAIR
    assert payload_cola["job_id"] == esc["job_id"]
    assert payload_cola["pr_ref"] == pr_mock.pr_id
    assert payload_cola["supera_umbral"] == "1"

    # 5. Verificar reflejo en expediente y actividad del ticket
    headers_portal = _cabeceras_portal(esc["user_id"], tenant_a)
    r_exp = await client.get(f"/api/v1/audit/tickets/{esc['numero']}", headers=headers_portal)
    assert r_exp.status_code == 200, r_exp.text
    exp = r_exp.json()
    assert len(exp["reparaciones_selectores"]) == 1
    rep = exp["reparaciones_selectores"][0]
    assert rep["selector_antiguo"] == "#btn-pagar-old"
    assert rep["selector_propuesto"] == '[data-testid="checkout.pay.btn-submit"]'
    assert rep["confianza"] == pytest.approx(resultado.applied_candidate.confidence)
    assert rep["prueba_afectada"] == spec_rel
    assert rep["pr_ref"] == pr_mock.pr_id
    assert rep["supera_umbral"] is True

    r_act = await client.get(f"/api/v1/tickets/{esc['numero']}/activity", headers=headers_portal)
    assert r_act.status_code == 200, r_act.text
    eventos_act = [item["evento"] for item in r_act.json()["items"]]
    assert any(
        pr_mock.pr_id in ev and f"{resultado.applied_candidate.confidence:.2f}" in ev
        for ev in eventos_act
    )


async def test_selector_repair_bajo_umbral_sin_pr_encola_mantenimiento_sin_detener_job(
    client_interna: AsyncClient,
    client: AsyncClient,
    seed_dos_tenants,
    tmp_path: Path,
) -> None:
    """Con confianza < 0.8 Testing escala a human_review sin PR y el job sigue activo."""
    tenant_a, _ = seed_dos_tenants
    esc = await _preparar_escenario(tenant_a)

    spec_rel = "e2e/tests/login.spec.ts"
    spec_file = tmp_path / spec_rel
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "test('login', async ({ page }) => {\n"
        "  await page.locator('.btn-legacy-login').click();\n"
        "});\n",
        encoding="utf-8",
    )

    class _LowConfidenceStub:
        def propose_candidates(self, _req: RepairRequestDTO) -> list[SelectorCandidateDTO]:
            return [
                SelectorCandidateDTO(
                    selector='[name="login_submit"]',
                    strategy="legacy-attr",
                    confidence=0.68,
                )
            ]

    engine = SelectorRepairEngine(llm_stub=_LowConfidenceStub(), confidence_floor=0.8)
    resultado = engine.repair_selector(
        RepairRequestDTO(
            spec_path=spec_rel,
            line_number=2,
            broken_selector=".btn-legacy-login",
            step_intent="Enviar formulario de inicio de sesión",
            dom_excerpt='<button name="login_submit">Entrar</button>',
        ),
        base_dir=tmp_path,
    )
    assert resultado.status == "human_review"
    assert resultado.repaired_selector is None
    assert resultado.branch_name is None

    candidato_bajo = resultado.evaluated_candidates[0]
    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock(return_value="1700000000001-0")

    with patch("kallicode_api.routers.internal.redis_cliente", return_value=mock_redis):
        resp = await client_interna.post(
            "/internal/v1/escalations",
            headers=_cabeceras_svc(tenant_a, f"sr-low-{uuid.uuid4().hex}"),
            json={
                "job_id": esc["job_id"],
                "etapa": "qa",
                "paso": "qa.e2e.AC-2",
                "motivo": "selector_repair",
                "severidad": "media",
                "detalle": {
                    "selector_antiguo": ".btn-legacy-login",
                    "selector_propuesto": candidato_bajo["selector"],
                    "confianza": candidato_bajo["confidence"],
                    "prueba_afectada": spec_rel,
                    "pr_ref": None,
                },
            },
        )

    assert resp.status_code in (200, 201), resp.text
    cuerpo = resp.json()
    assert cuerpo["estado"] == "abierta"
    assert cuerpo["cola"] == STREAM_SELECTOR_REPAIR
    assert cuerpo["job_detenido"] is False
    assert cuerpo["supera_umbral"] is False

    # El job del ticket de producto tampoco se detiene por debajo del umbral
    async with sesion_sistema() as db:
        estado_job = (
            await db.execute(
                text("SELECT estado FROM core.jobs WHERE id=:j"),
                {"j": esc["job_id"]},
            )
        ).scalar()
        assert estado_job == "qa"

    mock_redis.xadd.assert_awaited_once()
    stream_usado, payload_cola = mock_redis.xadd.await_args.args[:2]
    assert stream_usado == STREAM_SELECTOR_REPAIR
    assert payload_cola["pr_ref"] == ""
    assert payload_cola["supera_umbral"] == "0"

    headers_portal = _cabeceras_portal(esc["user_id"], tenant_a)
    r_exp = await client.get(f"/api/v1/audit/tickets/{esc['numero']}", headers=headers_portal)
    assert r_exp.status_code == 200, r_exp.text
    rep = r_exp.json()["reparaciones_selectores"][0]
    assert rep["confianza"] == pytest.approx(0.68)
    assert rep["pr_ref"] is None
    assert rep["supera_umbral"] is False

    r_act = await client.get(f"/api/v1/tickets/{esc['numero']}/activity", headers=headers_portal)
    assert r_act.status_code == 200, r_act.text
    eventos_act = [item["evento"] for item in r_act.json()["items"]]
    assert any("0.68" in ev and "sin PR" in ev for ev in eventos_act)
