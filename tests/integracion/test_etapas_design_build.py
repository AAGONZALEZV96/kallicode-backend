"""KC-BE-06-1: Integración de Triage -> Design -> Gate1 y Build con contexto de grafo y LOC."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from kallicode_api.main_interna import crear_app_interna
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.grafo import GrafoClienteDoble, Subgrafo
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.llm.plantillas import PLANTILLAS
from kallicode_core.llm.tiers import Tier
from kallicode_workers.orquestador import build, design, etapas
from kallicode_workers.orquestador.cliente_interno import ClienteInterno
from sqlalchemy import text

pytestmark = pytest.mark.integracion


def _res(paso_id: str, salida: dict, tier: Tier | None = None) -> ResultadoPaso:
    pl = PLANTILLAS[paso_id]
    tier_efectivo = tier or pl["tier"]
    return ResultadoPaso(
        paso_id=paso_id,
        version=pl["version"],
        tier=tier_efectivo,
        modelo=f"mock-{tier_efectivo.value}",
        salida=salida,
        confianza=0.95,
        validacion="esquema_valido",
        tokens_in=120,
        tokens_out=80,
        duracion_ms=45,
    )


@pytest_asyncio.fixture
async def entorno_pipeline(redis_doble, monkeypatch):
    """Prepara organización, suscripción, ticket y job en triage, y limpia al salir."""
    monkeypatch.setattr(get_settings(), "persistir_tier_fable", True)

    tenant_id = f"org_kc06_{uuid.uuid4().hex[:10]}"
    plan_id = f"plan_{uuid.uuid4().hex[:8]}"
    ticket_id = f"tk_{uuid.uuid4().hex[:12]}"
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    numero = "KC-601"
    pr_url = f"https://github.com/kallicode/repo/pull/{uuid.uuid4().int % 100000}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Org KC-BE-06-1')"),
            {"id": tenant_id},
        )
        await db.execute(
            text(
                "INSERT INTO core.plans (codigo, nombre, storage_mb) VALUES (:p, 'Plan KC06', 1000)"
            ),
            {"p": plan_id},
        )
        await db.execute(
            text(
                "INSERT INTO core.subscriptions "
                "(id, tenant_id, plan_codigo, estado, inicia_el, renueva_el) "
                "VALUES (:id, :t, :p, 'activa', current_date, current_date + 30)"
            ),
            {"id": f"sub_{tenant_id}", "t": tenant_id, "p": plan_id},
        )
        await db.execute(
            text(
                "INSERT INTO core.tickets "
                "(id, tenant_id, numero, titulo, descripcion, tipo, prioridad, etapa, pr_url) "
                "VALUES (:id, :t, :num, 'Fallo en validador', 'Descripción detallada', "
                "'bug', 'media', 'triage', :pr)"
            ),
            {"id": ticket_id, "t": tenant_id, "num": numero, "pr": pr_url},
        )
        await db.execute(
            text(
                "INSERT INTO core.jobs "
                "(id, tenant_id, ticket_id, estado, prioridad, iteraciones, contexto) "
                "VALUES (:jid, :t, :tk, 'triage', 'P2', '{\"triage\": 1}'::jsonb, "
                "CAST(:ctx AS jsonb))"
            ),
            {
                "jid": job_id,
                "t": tenant_id,
                "tk": ticket_id,
                "ctx": (
                    '{"titulo": "Fallo en validador", "origen": "portal", '
                    '"descripcion": "Descripción detallada", '
                    '"contracts": [{"name": "api-v1"}], '
                    '"archivos": {"src/validador.py": "def validar():\\n    return False\\n"}, '
                    '"pr_url": "' + pr_url + '"}'
                ),
            },
        )
        await db.commit()

    app_interna = crear_app_interna()
    cliente = ClienteInterno(base_url="http://localhost", servicio="orquestador")
    await cliente._http.aclose()
    cliente._http = httpx.AsyncClient(
        transport=ASGITransport(app=app_interna),
        base_url="http://localhost/internal/v1",
        headers={"Host": "localhost"},
    )

    try:
        yield {
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "job_id": job_id,
            "numero": numero,
            "pr_url": pr_url,
            "cliente": cliente,
        }
    finally:
        await cliente.cerrar()
        async with sesion_sistema() as db:
            await db.execute(
                text("DELETE FROM core.usage_loc WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(
                text("DELETE FROM core.usage_cycles WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(
                text("DELETE FROM core.llm_steps WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(
                text(
                    "DELETE FROM core.spec_alternatives WHERE spec_id IN (SELECT id FROM core.specs WHERE tenant_id = :t)"
                ),
                {"t": tenant_id},
            )
            await db.execute(
                text(
                    "DELETE FROM core.spec_criteria WHERE spec_id IN (SELECT id FROM core.specs WHERE tenant_id = :t)"
                ),
                {"t": tenant_id},
            )
            await db.execute(text("DELETE FROM core.specs WHERE tenant_id = :t"), {"t": tenant_id})
            await db.execute(
                text("DELETE FROM core.job_transitions WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(text("DELETE FROM core.jobs WHERE tenant_id = :t"), {"t": tenant_id})
            await db.execute(
                text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(
                text("DELETE FROM core.subscriptions WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})
            await db.commit()


@pytest.mark.asyncio
async def test_flujo_integracion_triage_a_gate1_y_build_con_grafo_y_loc(
    entorno_pipeline,
) -> None:
    """Lleva un job de triage a gate1, aprueba la spec y ejecuta build verificando BD."""
    ctx = entorno_pipeline
    tenant_id = ctx["tenant_id"]
    ticket_id = ctx["ticket_id"]
    job_id = ctx["job_id"]
    numero = ctx["numero"]
    cliente = ctx["cliente"]

    doble_grafo = GrafoClienteDoble()
    doble_grafo.registrar_subgrafo(
        numero,
        Subgrafo(
            nodos=({"id": "src/validador.py", "kind": "module"},),
            aristas=(
                {
                    "from": "src/api.py",
                    "to": "src/validador.py",
                    "direction": "inbound",
                },
            ),
        ),
    )

    respuestas_llm = {
        "triage.classify": _res(
            "triage.classify",
            {
                "type": "bug",
                "summary_en": "Validator returns False on valid input",
                "symptoms": [{"symptom": "false rejection", "context": "login"}],
                "confidence": 0.95,
            },
            tier=Tier.FLASH,
        ),
        "triage.dedup": _res(
            "triage.dedup",
            {"duplicate_of": None, "related_to": []},
            tier=Tier.FLASH,
        ),
        "triage.impact": _res(
            "triage.impact",
            {
                "affected_modules": ["src/validador.py"],
                "impact_radius": "low",
                "regression_risk": 0.2,
                "priority": "P2",
                "estimated_complexity": "small",
                "recommended_tier_downstream": "pro",
                "reasoning_summary_en": "Isolated validator module",
            },
            tier=Tier.FLASH,
        ),
        "design.digest": _res(
            "design.digest",
            {
                "design_brief_en": "Fix boolean return in src/validador.py",
                "hard_constraints": ["Keep public function signature"],
                "relevant_entities": ["validar"],
            },
            tier=Tier.FLASH,
        ),
        "design.alternatives": _res(
            "design.alternatives",
            {
                "alternatives": [
                    {
                        "id": "ALT-1",
                        "approach_en": "Direct fix in validar()",
                        "risks": ["minimal"],
                        "est_size": "small",
                    },
                    {
                        "id": "ALT-2",
                        "approach_en": "Refactor validator class",
                        "risks": ["higher churn", "api change", "migration"],
                        "est_size": "large",
                    },
                ]
            },
            tier=Tier.PRO,
        ),
        "design.adversarial_review": _res(
            "design.adversarial_review",
            {
                "chosen_id": "ALT-1",
                "decision_rationale_en": "ALT-1 is minimal and safe",
                "adr_draft": {
                    "title": "ADR-001 Direct Validator Fix",
                    "context": "Validator bug",
                    "decision": "Use ALT-1",
                    "consequences": "Low risk",
                },
            },
            tier=Tier.PRO,
        ),
        "design.compile": _res(
            "design.compile",
            {
                "spec_md": "# Spec KC-601\nFix `src/validador.py`.",
                "acceptance_criteria": [
                    {
                        "id": "AC-1",
                        "given": "un payload valido",
                        "when": "se invoca validar()",
                        "then": "retorna True",
                    }
                ],
                "scope_paths": ["src/"],
                "test_plan": [
                    {
                        "nombre": "test_validador",
                        "tipo": "unitario",
                        "comando": "pytest tests/test_validador.py",
                        "criterios": ["AC-1"],
                    }
                ],
                "migration_steps": [],
            },
            tier=Tier.PRO,
        ),
        "build.plan": _res(
            "build.plan",
            {
                "steps": [
                    {
                        "order": 1,
                        "file": "src/validador.py",
                        "change_summary_en": "Return True when valid",
                        "complexity": "flash",
                    }
                ]
            },
            tier=Tier.PRO,
        ),
        "build.diff": _res(
            "build.diff",
            {
                "diff": (
                    "--- a/src/validador.py\n"
                    "+++ b/src/validador.py\n"
                    "@@ -1,2 +1,3 @@\n"
                    " def validar():\n"
                    "-    return False\n"
                    "+    es_valido = True\n"
                    "+    return es_valido\n"
                ),
                "rationale_en": "Fix return value",
                "needs_replan": False,
            },
            tier=Tier.FLASH,
        ),
        "build.selfreview": _res(
            "build.selfreview",
            {
                "verdict": "ready_for_qa",
                "criteria_coverage": ["AC-1"],
                "blockers": [],
            },
            tier=Tier.PRO,
        ),
    }

    entradas_capturadas: dict[str, dict] = {}
    blobs_memoria: dict[str, str] = {}

    async def _mock_ejecutar_paso(paso_id: str, entrada: dict, **kwargs) -> ResultadoPaso:
        entradas_capturadas[paso_id] = entrada
        return respuestas_llm[paso_id]

    async def _mock_subir(
        job: dict,
        *,
        etapa: str,
        intento: int,
        paso: str,
        nombre: str,
        contenido: str,
        **kwargs,
    ) -> str:
        ref = f"pipeline/{job['tenant_id']}/{job['id']}/{etapa}/{intento}/{paso}/{nombre}"
        blobs_memoria[ref] = contenido
        return ref

    async def _mock_leer(ref: str) -> str:
        return blobs_memoria[ref]

    with (
        patch.object(etapas, "ejecutar_paso", side_effect=_mock_ejecutar_paso),
        patch.object(etapas, "_subir_artefacto", side_effect=_mock_subir),
        patch.object(etapas, "_leer_artefacto", side_effect=_mock_leer),
        patch("kallicode_api.routers.internal.registrar_evento", return_value="sello_mock"),
    ):
        # 1. Ejecutar Triage -> transiciona automáticamente el job a 'design'
        async with sesion_sistema() as db:
            job_triage = await uno(db, "SELECT * FROM core.jobs WHERE id = :id", {"id": job_id})
        await etapas.ejecutar_triage(dict(job_triage), cliente, intento=1, grafo=doble_grafo)

        async with sesion_sistema() as db:
            job_design = await uno(db, "SELECT * FROM core.jobs WHERE id = :id", {"id": job_id})
        assert job_design["estado"] == "design"

        # 2. Ejecutar Design -> publica spec y transiciona el job a 'gate1'
        await design.ejecutar_diseno(dict(job_design), cliente, intento=1, grafo=doble_grafo)

        async with sesion_sistema() as db:
            job_gate1 = await uno(db, "SELECT * FROM core.jobs WHERE id = :id", {"id": job_id})
            spec_row = await uno(
                db,
                "SELECT * FROM core.specs WHERE ticket_id = :tk AND tenant_id = :t",
                {"tk": ticket_id, "t": tenant_id},
            )
            alts_rows = await todos(
                db,
                "SELECT * FROM core.spec_alternatives WHERE spec_id = :sid ORDER BY alt_id",
                {"sid": spec_row["id"]},
            )
            crit_rows = await todos(
                db,
                "SELECT * FROM core.spec_criteria WHERE spec_id = :sid ORDER BY criterio_id",
                {"sid": spec_row["id"]},
            )

        assert job_gate1["estado"] == "gate1"
        assert spec_row is not None
        assert spec_row["version"] == 1
        assert spec_row["scope_paths"] == ["src/"]
        assert len(alts_rows) == 2
        assert sum(1 for a in alts_rows if a["recomendada"]) == 1
        assert len(crit_rows) == 1
        assert crit_rows[0]["criterio_id"] == "AC-1"
        assert crit_rows[0]["given_txt"] == "un payload valido"
        assert crit_rows[0]["when_txt"] == "se invoca validar()"
        assert crit_rows[0]["then_txt"] == "retorna True"

        # 3. Simular aprobación de Gate 1 para pasar a Build y probar rechazo fuera de scope + éxito
        async with sesion_sistema() as db:
            await db.execute(
                text("UPDATE core.specs SET estado = 'aprobada' WHERE id = :sid"),
                {"sid": spec_row["id"]},
            )
            await db.execute(
                text(
                    "UPDATE core.jobs SET estado = 'build', "
                    "iteraciones = jsonb_set(iteraciones, '{build}', '1'::jsonb) "
                    "WHERE id = :jid"
                ),
                {"jid": job_id},
            )
            await db.commit()

        async with sesion_sistema() as db:
            job_build = await uno(db, "SELECT * FROM core.jobs WHERE id = :id", {"id": job_id})

        # 3a. Probar que un diff fuera de scope_paths devuelve needs_replan
        diff_original = respuestas_llm["build.diff"]
        respuestas_llm["build.diff"] = _res(
            "build.diff",
            {
                "diff": "--- a/infra/secrets.txt\n+++ b/infra/secrets.txt\n@@ -1 +1 @@\n-old\n+new\n",
                "needs_replan": False,
            },
            tier=Tier.FLASH,
        )
        replan_res = await build.ejecutar_build(
            dict(job_build), cliente, intento=1, grafo=doble_grafo
        )
        assert replan_res is not None
        assert replan_res["needs_replan"] is True

        # 3b. Ejecutar Build válido en el intento 2
        respuestas_llm["build.diff"] = diff_original
        resultado_build = await build.ejecutar_build(
            dict(job_build), cliente, intento=2, grafo=doble_grafo
        )
        assert resultado_build is None

    # 4. Verificar que las 4 etapas de Design/Build con grafo recibieron el subgrafo grabado
    assert entradas_capturadas["design.digest"]["contexto"]["subgrafo"]["nodes"] == [
        {"id": "src/validador.py", "kind": "module"}
    ]
    assert entradas_capturadas["design.adversarial_review"]["subgrafo_dependencias"][
        "inbound_deps"
    ] == [{"from": "src/api.py", "to": "src/validador.py", "direction": "inbound"}]
    assert entradas_capturadas["build.plan"]["contexto_archivos"]["subgrafo"]["nodes"] == [
        {"id": "src/validador.py", "kind": "module"}
    ]
    assert entradas_capturadas["build.diff"]["callers"]["edges"] == [
        {"from": "src/api.py", "to": "src/validador.py", "direction": "inbound"}
    ]

    # 5. Verificar en BD los pasos contabilizados en core.llm_steps y el registro en core.usage_loc
    async with sesion_sistema() as db:
        pasos_db = await todos(
            db,
            "SELECT etapa, paso, tier FROM core.llm_steps WHERE job_id = :jid ORDER BY creado_en",
            {"jid": job_id},
        )
        loc_row = await uno(
            db,
            "SELECT * FROM core.usage_loc WHERE ticket_id = :tk AND tenant_id = :t",
            {"tk": ticket_id, "t": tenant_id},
        )

    nombres_pasos = [p["paso"] for p in pasos_db]
    assert "design.digest" in nombres_pasos
    assert "design.alternatives" in nombres_pasos
    assert "design.adversarial_review" in nombres_pasos
    assert "design.compile" in nombres_pasos
    assert "build.plan" in nombres_pasos
    assert "build.diff.1" in nombres_pasos
    assert "build.selfreview" in nombres_pasos

    assert loc_row is not None
    assert loc_row["anadidas"] == 2
    assert loc_row["eliminadas"] == 1
    assert loc_row["netas"] == 1
