"""Etapa de Deploy del orquestador: preflight (4 condiciones) + 3 plantillas.

El preflight es determinista: el endpoint /deploy/preflight calcula las
4 condiciones en el servidor y devuelve go/no_go. deploy.preflight_digest
solo redacta el rollback_plan_ref a partir del veredicto ya calculado; no
decide go/no_go. deploy.anomaly se invoca solo si el deploy reporta anomalía
y deploy.changelog al cerrar el ciclo.
"""

from __future__ import annotations

from typing import Any

from kallicode_core.db import sesion_sistema, uno
from kallicode_core.logging import log

from ._comun import ejecutar_y_publicar, subir_artefacto
from .cliente_interno import ClienteInterno


async def _cargar_ticket(job: dict[str, Any]) -> dict[str, Any]:
    async with sesion_sistema() as db:
        t = await uno(
            db,
            """SELECT id, numero, titulo FROM core.tickets
               WHERE id = :tk AND tenant_id = :t""",
            {"tk": job["ticket_id"], "t": job["tenant_id"]},
        )
    if t is None:
        raise ValueError(f"Ticket {job['ticket_id']} no encontrado para deploy.")
    return dict(t)


async def ejecutar_deploy(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: Any = None,
) -> dict[str, Any] | None:
    """Orquesta las 3 plantillas de deploy y el preflight de 4 condiciones."""
    tenant_id = job["tenant_id"]
    job_id = job["id"]
    ticket = await _cargar_ticket(job)
    contexto = job.get("contexto") or {}
    destino = contexto.get("destino", "staging")

    preflight = await cliente.publicar_preflight(
        numero=ticket["numero"],
        tenant_id=tenant_id,
        destino=destino,
        intento=intento,
    )
    veredicto = preflight.get("veredicto", "no_go")
    faltantes = preflight.get("faltantes", [])

    digest = await ejecutar_y_publicar(
        "deploy.preflight_digest",
        {
            "gate_records": contexto.get("gate_records", {}),
            "security_verdict": contexto.get("security_verdict", {}),
            "pipeline_status": {"veredicto": veredicto, "faltantes": faltantes},
        },
        job=job,
        cliente=cliente,
        etapa="deploy",
        intento=intento,
    )
    rollback_ref = digest.salida.get("rollback_plan_ref")
    if rollback_ref:
        await subir_artefacto(
            job,
            etapa="deploy",
            intento=intento,
            paso="deploy.preflight_digest",
            nombre="rollback_plan.md",
            contenido=str(rollback_ref),
            content_type="text/markdown",
        )

    if veredicto != "go":
        log.warning(
            "orquestador.preflight_no_go",
            job_id=job_id,
            tenant_id=tenant_id,
            faltantes=faltantes,
        )
        return None

    resultado_post = contexto.get("deploy_resultado", "exito")
    if resultado_post == "anomalia" and contexto.get("anomalia"):
        await ejecutar_y_publicar(
            "deploy.anomaly",
            {
                "metrica": contexto["anomalia"].get("metrica", {}),
                "logs": contexto["anomalia"].get("logs", ""),
                "subgrafo": contexto.get("subgrafo", {}),
            },
            job=job,
            cliente=cliente,
            etapa="deploy",
            intento=intento,
        )

    idioma = contexto.get("idioma", "es")
    await ejecutar_y_publicar(
        "deploy.changelog",
        {
            "ticket": {"numero": ticket["numero"], "titulo": ticket["titulo"]},
            "spec_ref": contexto.get("spec_ref", ""),
            "deploy_result": {"resultado": resultado_post, "destino": destino},
            "idioma": idioma,
        },
        job=job,
        cliente=cliente,
        etapa="deploy",
        intento=intento,
    )

    log.info(
        "orquestador.deploy_completado",
        job_id=job_id,
        tenant_id=tenant_id,
        destino=destino,
        veredicto=veredicto,
    )
    return None
