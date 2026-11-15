"""Etapa de QA del orquestador: 4 plantillas + publicación de evidencia.

El backend deriva casos, digiere resultados, verifica visual y diagnostica
fallos. La EJECUCIÓN de las pruebas es del motor de Testing: se invoca por
su contrato y la matriz se publica por POST /internal/v1/tickets/{numero}/evidence
con idempotencia (ver ADR 004 de frontera Backend↔Testing).
"""

from __future__ import annotations

from typing import Any, Protocol

from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.logging import log

from ._comun import ejecutar_y_publicar, grafo_dict
from .cliente_interno import ClienteInterno


class MotorPruebas(Protocol):
    """Contrato mínimo del motor de pruebas de Testing."""

    async def ejecutar(self, casos: list[dict], *, tenant_id: str) -> dict[str, Any]:
        """Devuelve {'matrix': [...], 'suites_summary': {...}, 'flaky': [...]}."""
        ...


class MotorPruebasNulo:
    """Implementación por defecto: lee la matriz del contexto del job."""

    def __init__(self, matriz: list[dict], regresion: dict, flaky: list) -> None:
        self._matriz = matriz
        self._regresion = regresion
        self._flaky = flaky

    async def ejecutar(self, casos: list[dict], *, tenant_id: str) -> dict[str, Any]:
        return {
            "matrix": self._matriz,
            "suites_summary": {"passed": len(self._matriz), "failed": 0, "skipped": 0},
            "flaky": self._flaky,
            "regresion": self._regresion,
        }


def _motor_desde_contexto(job: dict[str, Any]) -> MotorPruebas:
    ctx = job.get("contexto") or {}
    return MotorPruebasNulo(
        matriz=list(ctx.get("qa_matrix") or []),
        regresion=dict(ctx.get("qa_regresion") or {}),
        flaky=list(ctx.get("qa_flaky") or []),
    )


async def _cargar_criterios(job: dict[str, Any]) -> list[dict[str, Any]]:
    async with sesion_sistema() as db:
        spec = await uno(
            db,
            """SELECT id FROM core.specs
               WHERE tenant_id = :t AND ticket_id = :tk AND estado = 'aprobada'
               ORDER BY version DESC LIMIT 1""",
            {"t": job["tenant_id"], "tk": job["ticket_id"]},
        )
        if spec is None:
            raise ValueError(f"El ticket {job['ticket_id']} no tiene spec aprobada para QA.")
        criterios = await todos(
            db,
            """SELECT criterio_id AS id, given_txt AS given, when_txt AS "when",
                      then_txt AS then
               FROM core.spec_criteria WHERE tenant_id = :t AND spec_id = :s
               ORDER BY criterio_id""",
            {"t": job["tenant_id"], "s": spec["id"]},
        )
    return criterios


async def ejecutar_qa(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: Any = None,
    motor: MotorPruebas | None = None,
) -> dict[str, Any] | None:
    """Orquesta las 4 plantillas de QA y publica la evidencia."""
    tenant_id = job["tenant_id"]
    job_id = job["id"]
    ticket_id = job["ticket_id"]

    criterios = await _cargar_criterios(job)
    if not criterios:
        raise ValueError("La spec no tiene criterios de aceptación para QA.")

    contexto = job.get("contexto") or {}
    derivados = await ejecutar_y_publicar(
        "qa.derive_cases",
        {
            "criterios": criterios,
            "fixtures": contexto.get("fixtures", []),
            "incidentes": contexto.get("incidentes", []),
        },
        job=job,
        cliente=cliente,
        etapa="qa",
        intento=intento,
    )
    casos = derivados.salida.get("cases", [])
    if not casos:
        raise ValueError("qa.derive_cases no generó casos ejecutables.")

    motor = motor or _motor_desde_contexto(job)
    crudo = await motor.ejecutar(casos, tenant_id=tenant_id)

    digerido = await ejecutar_y_publicar(
        "qa.results_digest",
        {
            "ci_output": crudo.get("suites_summary", {}),
            "regression_output": crudo.get("regresion", {}),
            "flow_results": crudo.get("matrix", []),
        },
        job=job,
        cliente=cliente,
        etapa="qa",
        intento=intento,
    )
    matriz = digerido.salida.get("matrix", [])
    flaky = digerido.salida.get("flaky", [])

    veredictos_visuales: list[dict[str, Any]] = []
    visual_ctx = contexto.get("qa_visual") or {}
    for criterio in criterios:
        evidencia = visual_ctx.get(criterio["id"])
        if not evidencia:
            continue
        visual = await ejecutar_y_publicar(
            "qa.visual_check",
            {
                "criterio": criterio,
                "estado_esperado": criterio.get("then"),
                "evidencia": evidencia,
            },
            job=job,
            cliente=cliente,
            etapa="qa",
            intento=intento,
            registro_id=f"qa.visual_check.{criterio['id']}",
        )
        veredictos_visuales.append(visual.salida)

    fallos = [m for m in matriz if m.get("status") == "fail"]
    if fallos:
        sub = await (grafo or _grafo_por_defecto()).subgrafo_ticket(
            tenant_id, job.get("ticket_numero") or ticket_id
        )
        diagnostico = await ejecutar_y_publicar(
            "qa.failure_diagnosis",
            {
                "fallo": fallos[0],
                "diff_hunks": contexto.get("diff_hunks", ""),
                "grafo": grafo_dict(sub),
            },
            job=job,
            cliente=cliente,
            etapa="qa",
            intento=intento,
        )
        accion = diagnostico.salida.get("action")
        if accion == "back_to_build":
            return {
                "needs_replan": True,
                "replan_reason_en": "QA failure diagnosis routed the job back to build.",
            }
        if accion == "escalate":
            await cliente.escalar(
                job_id=job_id,
                tenant_id=tenant_id,
                etapa="qa",
                motivo="validacion_persistente",
                severidad="alta",
                detalle={"diagnostico": diagnostico.salida},
                intento=intento,
            )
            return None

    numero = job.get("ticket_numero") or ticket_id
    await cliente.publicar_evidencia(
        numero=numero,
        tenant_id=tenant_id,
        corrida=intento,
        matriz=[
            {
                "criterio_id": m["test_id"],
                "resultado": _resultado_api(m["status"]),
                "extracto_fallo": m.get("raw_failure"),
                "evidencias": m.get("evidencias", []),
            }
            for m in matriz
        ],
        veredicto_visual=veredictos_visuales,
        regresion=crudo.get("regresion", {}),
        flaky=flaky,
        intento=intento,
    )

    log.info(
        "orquestador.qa_completado",
        job_id=job_id,
        tenant_id=tenant_id,
        criterios=len(criterios),
        fallos=len(fallos),
    )
    return None


def _resultado_api(status: str) -> str:
    return {"pass": "pasa", "fail": "falla", "flaky": "flaky", "skipped": "falla"}[status]


def _grafo_por_defecto() -> Any:
    from kallicode_core.grafo import grafo_cliente

    return grafo_cliente()
