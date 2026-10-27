"""Resolución de etapa y ejecución de sus pasos.

Sprint 4 (KC-BE-04-1): solo triage, con candidatos y subgrafo vacíos.
Sprint 6 (KC-BE-06-1/2): design, build, QA, seguridad y deploy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from kallicode_core.db import sesion_sistema, uno
from kallicode_core.grafo import GrafoCliente, grafo_cliente
from kallicode_core.llm import ejecutar_paso
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.logging import log
from kallicode_core.pipeline import EstadoJob

from .cliente_interno import ClienteInterno


async def ejecutar_triage(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> None:
    """Ejecuta los tres pasos de triage y publica el resultado agregado.

    En el Sprint 4:
      - triage.classify usa los datos del ticket.
      - triage.dedup recibe candidatos vacíos (la búsqueda vectorial por
        similitud no existe todavía en la capa de vectores; llega en un
        sprint posterior).
      - triage.impact recibe un subgrafo vacío del GrafoClienteDoble; la
        implementación real sobre el esquema cm llega en el Sprint 10
        (KC-BE-10-6).
    """
    tenant_id: str = job["tenant_id"]
    job_id: str = job["id"]
    ticket_id: str = job["ticket_id"]
    payload: dict = job.get("contexto") or {}

    # El endpoint POST /tickets/{numero}/triage-result espera el número
    # legible del ticket (KC-####), no su id interno (tk_...). El job
    # guarda ticket_id, pero no el numero: lo consultamos del ticket.
    async with sesion_sistema() as db:
        ticket_row = await uno(
            db,
            "SELECT numero FROM core.tickets WHERE id = :id AND tenant_id = :t",
            {"id": ticket_id, "t": tenant_id},
        )
    numero = ticket_row["numero"] if ticket_row else ticket_id

    # --- paso 1: classify ---
    r_classify = await ejecutar_paso(
        "triage.classify",
        {
            "ticket_id": numero,
            "titulo": payload.get("titulo", ""),
            "origen": payload.get("origen", "desconocido"),
            "descripcion": payload.get("descripcion", ""),
            "adjuntos_transcritos": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_classify),
        intento=intento,
    )

    # --- paso 2: dedup (candidatos vacíos en Sprint 4) ---
    r_dedup = await ejecutar_paso(
        "triage.dedup",
        {
            "ticket": r_classify.salida,
            "candidatos": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_dedup),
        intento=intento,
    )

    # --- paso 3: impact (subgrafo vía GrafoClienteDoble en Sprint 4) ---
    gc: GrafoCliente = grafo or grafo_cliente()
    sub = await gc.subgrafo_ticket(tenant_id, numero)
    r_impact = await ejecutar_paso(
        "triage.impact",
        {
            "ticket": {
                "classify": r_classify.salida,
                "dedup": r_dedup.salida,
            },
            "subgrafo": {
                "nodes": list(sub.nodos),
                "edges": list(sub.aristas),
                "inbound_deps": [],
            },
            "incidentes": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_impact),
        intento=intento,
    )

    # --- publicación agregada: triage-result ---
    await cliente.publicar_triage(
        numero=numero,
        tenant_id=tenant_id,
        resultado=_triage_result(r_classify, r_dedup, r_impact),
        intento=intento,
    )

    log.info(
        "orquestador.triage_completado",
        job_id=job_id,
        tenant_id=tenant_id,
        tipo=r_classify.salida.get("type"),
        prioridad=r_impact.salida.get("priority"),
        complejidad=r_impact.salida.get("estimated_complexity"),
    )


def _paso_dict(etapa: str, r: ResultadoPaso) -> dict[str, Any]:
    """Convierte un ResultadoPaso en el payload de StepIn del API interna.

    El campo `version_plantilla` del StepIn se rellena con `r.version`, que
    es el hash canónico de 12 caracteres de la plantilla ejecutada
    (calculado en plantillas.py). `reintentos` cuenta los intentos fallidos
    previos al éxito final.
    """
    return {
        "etapa": etapa,
        "paso": r.paso_id,
        "version_plantilla": r.version,
        "modelo": r.modelo,
        "tier": r.tier.value,
        "confianza": r.confianza,
        "validacion": r.validacion,
        "reintentos": len(r.intentos),
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "duracion_ms": r.duracion_ms,
        # Sprint 8 (KC-BE-08-4) sube los payloads reales a Blob; hasta
        # entonces el orquestador no persiste los cuerpos completos.
        "entrada_ref": f"mem://{r.paso_id}/in",
        "salida_ref": f"mem://{r.paso_id}/out",
    }


# Mapeo del `type` del catálogo LLM al `tipo` del CHECK de core.tickets
# (bug | mejora | funcionalidad | seguridad).
_TIPO_MODELO_A_ESQUEMA = {
    "bug": "bug",
    "improvement": "mejora",
    "feature": "funcionalidad",
    "requirement": "funcionalidad",
}


def _triage_result(
    r_classify: ResultadoPaso,
    r_dedup: ResultadoPaso,
    r_impact: ResultadoPaso,
) -> dict[str, Any]:
    """Construye el payload de TriageResultIn a partir de los tres pasos.

    Los nombres de las claves coinciden con el JSON Schema de cada plantilla
    (en inglés) y se traducen a los nombres del contrato interno del API
    (en español) en este único punto.
    """
    c = r_classify.salida
    d = r_dedup.salida
    i = r_impact.salida

    tipo_modelo = c.get("type", "bug")
    tipo_esquema = _TIPO_MODELO_A_ESQUEMA.get(tipo_modelo, "bug")

    return {
        "tipo": tipo_esquema,
        "prioridad": i.get("priority", "P3"),
        "duplicado_de": d.get("duplicate_of"),
        "relacionados": list(d.get("related_to") or []),
        "impacto": {
            "modulos": list(i.get("affected_modules") or []),
            "radio": i.get("impact_radius", "low"),
            "riesgo_regresion": i.get("regression_risk"),
            "razonamiento": i.get("reasoning_summary_en"),
        },
        "complejidad": i.get("estimated_complexity", "medium"),
        # La plantilla triage.impact produce `recommended_tier_downstream`
        # (en ese orden). Antes leíamos `tier_recommended_downstream`, que
        # siempre devolvía el default "flash" y perdía el tier real.
        "tier_recomendado": i.get("recommended_tier_downstream", "flash"),
    }


# Mapa etapa → función. El bucle lo consulta por el estado del job.
# Sprint 6 añadirá DESIGN, BUILD, QA, SEGURIDAD y DEPLOY.
ETAPAS: dict[EstadoJob, Callable[..., Awaitable[None]]] = {
    EstadoJob.TRIAGE: ejecutar_triage,
}
