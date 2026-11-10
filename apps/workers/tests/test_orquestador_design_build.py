from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from kallicode_core.grafo import GrafoClienteDoble, Subgrafo
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.llm.tiers import Tier
from kallicode_workers.orquestador import bucle, etapas

_JOB = {
    "id": "job_test",
    "tenant_id": "tenant_test",
    "ticket_id": "tk_test",
    "estado": "design",
    "contexto": {
        "contracts": [{"name": "ticket-api"}],
        "archivos": {"src/example.py": "old content"},
    },
}
_TICKET = {
    "id": "tk_test",
    "numero": "KC-1001",
    "tipo": "bug",
    "titulo": "Test ticket",
    "descripcion": "Description",
    "prioridad_pipeline": "P2",
    "impacto": {"modulos": ["src"]},
    "complejidad": "small",
    "pr_url": None,
}


def _resultado(paso_id: str, salida: dict, tier: Tier = Tier.FLASH) -> ResultadoPaso:
    return ResultadoPaso(
        paso_id=paso_id,
        version="version12abc",
        tier=tier,
        modelo=f"model-{tier.value}",
        salida=salida,
        confianza=0.9,
        validacion="esquema_valido",
        tokens_in=10,
        tokens_out=20,
        duracion_ms=100,
    )


def _doble_grafo() -> GrafoClienteDoble:
    doble = GrafoClienteDoble()
    doble.registrar_subgrafo(
        "KC-1001",
        Subgrafo(
            nodos=({"id": "src/example.py"},),
            aristas=({"from": "src/example.py", "to": "src/caller.py"},),
        ),
    )
    return doble


@pytest.mark.asyncio
async def test_diseno_ejecuta_las_cuatro_plantillas_y_publica_spec() -> None:
    resultados = {
        "design.digest": _resultado(
            "design.digest",
            {"design_brief_en": "brief", "hard_constraints": ["keep API"]},
        ),
        "design.alternatives": _resultado(
            "design.alternatives",
            {
                "alternatives": [
                    {
                        "id": "A",
                        "approach_en": "Approach A",
                        "risks": ["risk"],
                        "est_size": "small",
                    },
                    {"id": "B", "approach_en": "Approach B", "risks": [], "est_size": "medium"},
                ]
            },
            Tier.PRO,
        ),
        "design.adversarial_review": _resultado(
            "design.adversarial_review",
            {"chosen_id": "B", "decision_rationale_en": "safer", "adr_draft": {"title": "ADR"}},
            Tier.FABLE,
        ),
        "design.compile": _resultado(
            "design.compile",
            {
                "spec_md": "# Design",
                "acceptance_criteria": [
                    {"id": "AC-1", "given": "given", "when": "when", "then": "then"}
                ],
                "scope_paths": ["src/"],
            },
        ),
    }
    cliente = AsyncMock()
    cliente.publicar_paso = AsyncMock()
    cliente.publicar_spec = AsyncMock()
    grafo = _doble_grafo()

    async def ejecutar(paso_id: str, entrada: dict, **kwargs) -> ResultadoPaso:
        assert kwargs["tenant_id"] == _JOB["tenant_id"]
        assert kwargs["ticket_id"] == _JOB["ticket_id"]
        return resultados[paso_id]

    async def subir(job: dict, *, etapa: str, paso: str, nombre: str, **kwargs) -> str:
        return f"{etapa}/{paso}/{nombre}"

    with (
        patch.object(etapas, "_cargar_ticket", return_value=_TICKET),
        patch.object(etapas, "ejecutar_paso", side_effect=ejecutar) as llamar,
        patch.object(etapas, "_subir_artefacto", side_effect=subir),
    ):
        await etapas.ejecutar_diseno(_JOB, cliente, intento=1, grafo=grafo)

    assert [call.args[0] for call in llamar.call_args_list] == list(resultados)
    assert llamar.call_args_list[0].args[1]["contexto"]["subgrafo"]["nodes"]
    assert llamar.call_args_list[2].args[1]["subgrafo_dependencias"]["edges"]
    assert cliente.publicar_paso.await_count == 4
    paso_publicado = cliente.publicar_paso.await_args_list[0].kwargs["paso"]
    assert paso_publicado["entrada_ref"] != "mem://design.digest/in"
    assert paso_publicado["salida_ref"].endswith("salida.json")
    payload = cliente.publicar_spec.await_args.kwargs["spec"]
    assert payload["spec_md_ref"] == "design/spec/spec.md"
    assert sum(a["recomendada"] for a in payload["alternativas"]) == 1
    assert payload["alternativas"][1]["recomendada"] is True
    assert payload["criterios"] == [
        {"id": "AC-1", "given": "given", "when": "when", "then": "then"}
    ]


@pytest.mark.asyncio
async def test_build_rechaza_diff_fuera_de_scope_y_pide_replan() -> None:
    spec = {
        "spec_md_ref": "mem://spec",
        "scope_paths": ["src/"],
        "adr_borrador": None,
        "alternativas": [],
        "criterios": [],
    }
    respuestas = {
        "build.plan": _resultado(
            "build.plan",
            {
                "steps": [
                    {
                        "order": 1,
                        "file": "src/example.py",
                        "change_summary_en": "change",
                        "complexity": "flash",
                    }
                ]
            },
            Tier.PRO,
        ),
        "build.diff": _resultado(
            "build.diff",
            {
                "diff": "--- a/secrets.txt\n+++ b/secrets.txt\n@@ -1 +1 @@\n-old\n+new",
                "needs_replan": False,
            },
            Tier.FLASH,
        ),
    }
    cliente = AsyncMock()
    cliente.publicar_paso = AsyncMock()
    cliente.publicar_loc = AsyncMock()

    async def ejecutar(paso_id: str, entrada: dict, **kwargs) -> ResultadoPaso:
        return respuestas[paso_id]

    async def subir(job: dict, *, etapa: str, paso: str, nombre: str, **kwargs) -> str:
        return f"{etapa}/{paso}/{nombre}"

    with (
        patch.object(etapas, "_cargar_ticket", return_value=_TICKET),
        patch.object(etapas, "_cargar_spec_aprobada", return_value=spec),
        patch.object(etapas, "ejecutar_paso", side_effect=ejecutar),
        patch.object(etapas, "_subir_artefacto", side_effect=subir),
        patch.object(etapas, "_leer_artefacto", return_value="# spec"),
    ):
        resultado = await etapas.ejecutar_build(_JOB, cliente, intento=1)

    assert resultado is not None
    assert resultado["needs_replan"] is True
    assert "outside" in resultado["replan_reason_en"]
    assert cliente.publicar_paso.await_count == 2
    cliente.publicar_loc.assert_not_awaited()


def test_scope_rechaza_rutas_absolutas_y_traversal() -> None:
    assert not etapas._ruta_en_scope("../secrets.txt", ["src/"])
    assert not etapas._ruta_en_scope("/etc/passwd", ["src/"])
    assert not etapas._ruta_en_scope(r"C:\secrets.txt", ["src/"])
    assert etapas._ruta_en_scope("src/nested/file.py", ["src/"])


@pytest.mark.asyncio
@pytest.mark.parametrize("con_pr", [True, False])
async def test_build_calcula_loc_y_solo_publica_con_pr_real(con_pr: bool) -> None:
    ticket = {**_TICKET, "pr_url": "https://github.com/org/repo/pull/42" if con_pr else None}
    spec = {
        "spec_md_ref": "mem://spec",
        "scope_paths": ["src/"],
        "adr_borrador": None,
        "alternativas": [],
        "criterios": [],
    }
    respuestas = {
        "build.plan": _resultado(
            "build.plan",
            {
                "steps": [
                    {
                        "order": 1,
                        "file": "src/example.py",
                        "change_summary_en": "change",
                        "complexity": "flash",
                    }
                ]
            },
            Tier.PRO,
        ),
        "build.diff": _resultado(
            "build.diff",
            {
                "diff": (
                    "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1,2 @@\n-old\n+new\n+added"
                ),
                "needs_replan": False,
            },
            Tier.FLASH,
        ),
        "build.selfreview": _resultado(
            "build.selfreview",
            {"verdict": "ready_for_qa", "criteria_coverage": ["AC-1"]},
            Tier.FABLE,
        ),
    }
    cliente = AsyncMock()
    cliente.publicar_paso = AsyncMock()
    cliente.publicar_loc = AsyncMock()

    async def ejecutar(paso_id: str, entrada: dict, **kwargs) -> ResultadoPaso:
        if paso_id == "build.diff":
            assert kwargs["tier_inicial"] is Tier.FLASH
        return respuestas[paso_id]

    async def subir(job: dict, *, etapa: str, paso: str, nombre: str, **kwargs) -> str:
        return f"{etapa}/{paso}/{nombre}"

    with (
        patch.object(etapas, "_cargar_ticket", return_value=ticket),
        patch.object(etapas, "_cargar_spec_aprobada", return_value=spec),
        patch.object(etapas, "ejecutar_paso", side_effect=ejecutar) as llamar,
        patch.object(etapas, "_subir_artefacto", side_effect=subir),
        patch.object(etapas, "_leer_artefacto", return_value="# spec"),
    ):
        resultado = await etapas.ejecutar_build(
            _JOB,
            cliente,
            intento=1,
            grafo=_doble_grafo(),
        )

    assert resultado is None
    assert llamar.call_args_list[0].args[1]["contexto_archivos"]["subgrafo"]["nodes"]
    assert llamar.call_args_list[1].args[1]["callers"]["edges"]
    if con_pr:
        loc = cliente.publicar_loc.await_args.kwargs["loc"]
        assert loc["anadidas"] == 2
        assert loc["eliminadas"] == 1
        assert loc["netas"] == 1
    else:
        cliente.publicar_loc.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_replan_transiciona_el_job_a_design(monkeypatch) -> None:
    etapa = AsyncMock(return_value={"needs_replan": True, "replan_reason_en": "scope"})
    monkeypatch.setitem(bucle.ETAPAS, etapas.EstadoJob.BUILD, etapa)
    cliente = AsyncMock()

    await bucle.procesar_job(
        {
            "id": "job_test",
            "tenant_id": "tenant_test",
            "estado": "build",
            "iteraciones": {"build": 1},
        },
        cliente,
    )

    cliente.transicionar.assert_awaited_once_with(
        job_id="job_test",
        tenant_id="tenant_test",
        a="design",
        motivo="build_needs_replan",
        intento=1,
        contexto={"replan_reason_en": "scope"},
    )
    cliente.escalar.assert_not_awaited()
