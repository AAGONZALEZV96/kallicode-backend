"""Etapa de Seguridad del orquestador: 3 plantillas + publicación de findings.

Normaliza los hallazgos crudos de los escáneres deterministas (SAST/SCA/secretos),
evalúa explotabilidad uno a uno con el grafo, compila el veredicto del cambio
y lo publica por POST /internal/v1/tickets/{numero}/findings con idempotencia.
"""

from __future__ import annotations

from typing import Any

from kallicode_core.db import sesion_sistema, uno
from kallicode_core.logging import log

from ._comun import ejecutar_y_publicar, grafo_dict
from .cliente_interno import ClienteInterno


async def _cargar_ticket(job: dict[str, Any]) -> dict[str, Any]:
    async with sesion_sistema() as db:
        t = await uno(
            db,
            """SELECT id, numero FROM core.tickets
               WHERE id = :tk AND tenant_id = :t""",
            {"tk": job["ticket_id"], "t": job["tenant_id"]},
        )
    if t is None:
        raise ValueError(f"Ticket {job['ticket_id']} no encontrado para security.")
    return dict(t)


async def ejecutar_security(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: Any = None,
) -> dict[str, Any] | None:
    """Orquesta las 3 plantillas de seguridad y publica findings."""
    tenant_id = job["tenant_id"]
    job_id = job["id"]
    ticket = await _cargar_ticket(job)
    contexto = job.get("contexto") or {}

    normalizado = await ejecutar_y_publicar(
        "security.normalize",
        {
            "sast": contexto.get("sast", {}),
            "sca": contexto.get("sca", {}),
            "secrets": contexto.get("secrets", {}),
        },
        job=job,
        cliente=cliente,
        etapa="security",
        intento=intento,
    )
    hallazgos = normalizado.salida.get("findings", [])

    from kallicode_core.grafo import grafo_cliente

    gc = grafo or grafo_cliente()
    sub = await gc.subgrafo_ticket(tenant_id, ticket["numero"])
    grafo_ctx = grafo_dict(sub)

    veredictos: list[dict[str, Any]] = []
    for hallazgo in hallazgos:
        v = await ejecutar_y_publicar(
            "security.exploitability",
            {"hallazgo": hallazgo, "grafo": grafo_ctx},
            job=job,
            cliente=cliente,
            etapa="security",
            intento=intento,
            registro_id=f"security.exploitability.{hallazgo['id']}",
        )
        veredictos.append(v.salida)

    reporte = await ejecutar_y_publicar(
        "security.report",
        {"veredictos": veredictos, "nocturno": contexto.get("nocturno", [])},
        job=job,
        cliente=cliente,
        etapa="security",
        intento=intento,
    )
    veredicto_cambio = reporte.salida.get("change_verdict", "approved")

    hallazgos_api: list[dict[str, Any]] = []
    for hallazgo, v in zip(hallazgos, veredictos, strict=True):
        hallazgos_api.append(
            {
                "id": v.get("finding_id") or hallazgo.get("id"),
                "origen": _origen_api(hallazgo),
                "severidad_contextual": _severidad_api(v.get("contextual_severity")),
                "titulo": hallazgo.get("rule", "hallazgo"),
                "veredicto": _veredicto_api(v.get("verdict")),
                "justificacion": v.get("reachability_trace_en"),
                "fix_diff_ref": v.get("proposed_fix_diff"),
                "bloquea": bool(v.get("blocks_deploy")),
            }
        )

    await cliente.publicar_findings(
        numero=ticket["numero"],
        tenant_id=tenant_id,
        hallazgos=hallazgos_api,
        veredicto_cambio=veredicto_cambio,
        tickets_proactivos=[
            {"titulo": t[:240], "cuerpo": t[:20000]}
            for t in reporte.salida.get("proactive_tickets", [])
        ],
        intento=intento,
    )

    log.info(
        "orquestador.security_completado",
        job_id=job_id,
        tenant_id=tenant_id,
        veredicto=veredicto_cambio,
        hallazgos=len(hallazgos),
    )
    return None


def _origen_api(hallazgo: dict[str, Any]) -> str:
    cat = (hallazgo.get("category") or "").lower()
    if "secret" in cat:
        return "secrets"
    if "dep" in cat or "library" in cat:
        return "sca"
    return "sast"


def _severidad_api(s: str | None) -> str:
    return {"critical": "critica", "high": "alta", "medium": "media", "low": "baja"}.get(
        s or "", "media"
    )


def _veredicto_api(v: str | None) -> str:
    return "falso_positivo" if v == "false_positive" else "confirmado"
