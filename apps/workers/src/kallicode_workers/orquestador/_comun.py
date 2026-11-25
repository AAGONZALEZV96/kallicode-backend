"""Helpers compartidos por las etapas del orquestador.

Extraídos de etapas.py para que qa.py, security.py y deploy.py puedan
reutilizar la publicación de pasos y artefactos sin duplicar código.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.config import get_settings
from kallicode_core.ids import nuevo
from kallicode_core.llm import ejecutar_paso
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.llm.tiers import Tier

from .cliente_interno import ClienteInterno
from .trazas import publicar_trazas_paso, subir_traza_blob


def paso_dict(
    etapa: str,
    r: ResultadoPaso,
    *,
    step_id: str | None = None,
    paso_id: str | None = None,
    entrada_ref: str | None = None,
    salida_ref: str | None = None,
) -> dict[str, Any]:
    """Convierte un ResultadoPaso en el payload de StepIn del API interna."""
    return {
        "step_id": step_id,
        "etapa": etapa,
        "paso": paso_id or r.paso_id,
        "version_plantilla": r.version,
        "modelo": r.modelo,
        "tier": r.tier.value,
        "confianza": r.confianza,
        "validacion": r.validacion,
        "reintentos": len(r.intentos),
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "duracion_ms": r.duracion_ms,
        "consumo_registrado": r.consumo_registrado,
        "entrada_ref": entrada_ref or f"mem://{r.paso_id}/in",
        "salida_ref": salida_ref or f"mem://{r.paso_id}/out",
    }


async def subir_artefacto(
    job: dict[str, Any],
    *,
    etapa: str,
    intento: int,
    paso: str,
    nombre: str,
    contenido: str,
    content_type: str,
) -> str:
    """Sube un artefacto a Blob y devuelve su referencia."""
    settings = get_settings()
    tenant = quote(str(job["tenant_id"]), safe="")
    job_id = quote(str(job["id"]), safe="")
    paso_path = quote(paso, safe=".")
    ref = f"pipeline/{tenant}/{job_id}/{etapa}/{intento}/{paso_path}/{nombre}"
    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)
        if settings.env == "local":
            try:
                await contenedor.create_container()
            except ResourceExistsError:
                pass
        objeto = contenedor.get_blob_client(ref)
        await objeto.upload_blob(
            contenido.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    return ref


async def ejecutar_y_publicar(
    paso_id: str,
    entrada: dict[str, Any],
    *,
    job: dict[str, Any],
    cliente: ClienteInterno,
    etapa: str,
    intento: int,
    registro_id: str | None = None,
    tier_inicial: Tier | None = None,
    subir_fn: Any = None,
) -> ResultadoPaso:
    """Ejecuta un paso LLM, sube entrada/salida a Blob con ruta determinista y publica el StepIn."""
    resultado = await ejecutar_paso(
        paso_id,
        entrada,
        tier_inicial=tier_inicial,
        tenant_id=job["tenant_id"],
        ticket_id=job["ticket_id"],
    )
    step_id = nuevo("st")
    ticket_ref = str(job.get("ticket_numero") or job["ticket_id"])
    entrada_ref, salida_ref = await publicar_trazas_paso(
        tenant_id=job["tenant_id"],
        ticket_ref=ticket_ref,
        step_id=step_id,
        entrada=entrada,
        salida=resultado.salida,
        subir_fn=subir_fn or subir_traza_blob,
    )
    await cliente.publicar_paso(
        job_id=job["id"],
        tenant_id=job["tenant_id"],
        paso=paso_dict(
            etapa,
            resultado,
            step_id=step_id,
            paso_id=registro_id,
            entrada_ref=entrada_ref,
            salida_ref=salida_ref,
        ),
        intento=intento,
    )
    return resultado


def grafo_dict(subgrafo: Any) -> dict[str, Any]:
    """Serializa un subgrafo del GrafoCliente al formato que consumen las plantillas."""
    nodos = list(subgrafo.nodos)
    aristas = list(subgrafo.aristas)
    return {
        "nodes": nodos,
        "edges": aristas,
        "inbound_deps": [a for a in aristas if a.get("direction") == "inbound"],
    }
