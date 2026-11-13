"""Fase documental del pipeline: presupuesto, stats y consumo (KC-CM-08-5 / KC-CM-08-6).

Une lo que comparten el build completo (build_graph.py) y el refresco diario (refresh.py):

* `construir_control_docs`: arma el control de presupuesto desde Settings y el plan del tenant.
* `registrar_resultado_documental`: deja en las stats del snapshot los documentos procesados,
  omitidos y fallidos (y el presupuesto), y el consumo en core.usage_tokens con origen propio.
* `refrescar_capa_documental`: el paso documental del refresco (purga, hash, ingesta).

Superar el presupuesto documental detiene solo esta fase: el grafo de código ya construido sigue
siendo utilizable y los documentos sin procesar quedan pendientes.
"""

from __future__ import annotations

from typing import Any

from .budget import ControlPresupuestoDocs
from .config import Settings
from .embeddings import TEIEmbeddingClient
from .graph_loader import GraphLoader
from .ingest_docs import (
    LectorBlob,
    LectorBlobHttp,
    ResumenDocumental,
    refrescar_documentos,
)
from .logging_setup import get_logger

logger = get_logger()

MB = 1024 * 1024
CLAVE_STATS = "documentos"


def construir_control_docs(loader: GraphLoader, settings: Settings) -> ControlPresupuestoDocs:
    """Control de presupuesto: tokens y tiempo desde Settings, tamaño desde docs_mb del plan."""
    limite_mb = loader.obtener_limite_docs_mb()
    return ControlPresupuestoDocs(
        tokens_max=settings.docs_presupuesto_tokens,
        segundos_max=float(settings.docs_presupuesto_segundos),
        bytes_max=None if limite_mb is None else limite_mb * MB,
        bytes_pendientes=loader.obtener_bytes_documentos_pendientes(),
    )


def stats_documentales(
    resumen: ResumenDocumental | None, control: ControlPresupuestoDocs
) -> dict[str, Any]:
    """Bloque `documentos` de cm.repo_snapshots.stats."""
    datos: dict[str, Any] = resumen.a_stats() if resumen else {}
    datos["presupuesto"] = control.resumen()
    return datos


def registrar_resultado_documental(
    loader: GraphLoader,
    snapshot_id: str | None,
    control: ControlPresupuestoDocs,
    resumen: ResumenDocumental | None = None,
    *,
    resultados: list[Any] | None = None,
    pendientes_restantes: int = 0,
) -> dict[str, Any]:
    """Escribe las stats del snapshot y el consumo en core.usage_tokens. Devuelve las stats.

    Acepta un `ResumenDocumental` (refresco) o la lista de resultados de la ingesta de pendientes
    (build completo). Nunca lanza: contabilizar no debe tumbar un grafo ya construido.
    """
    if resumen is None:
        resumen = ResumenDocumental(
            resultados=list(resultados or []), pendientes_restantes=pendientes_restantes
        )
    resumen.pendientes_restantes = max(
        resumen.pendientes_restantes, control.documentos_sin_procesar
    )
    stats = stats_documentales(resumen, control)
    if control.excedido:
        logger.warning(
            "presupuesto_documental_excedido: la ingesta documental se detuvo; el resto del grafo "
            "queda utilizable",
            extra={
                "tenant_id": loader.tenant_id,
                "codigo": control.codigo,
                "detalle": control.motivo,
                "documentos_pendientes": resumen.pendientes_restantes,
            },
        )
    try:
        if snapshot_id:
            loader.registrar_stats_snapshot(snapshot_id, CLAVE_STATS, stats)
        loader.registrar_consumo_documental(
            snapshot_id, control.real_tokens, control.invocaciones, control.real_ms
        )
    except Exception as exc:  # contabilidad best-effort
        logger.warning(
            "no se pudieron registrar las stats o el consumo documental",
            extra={"tenant_id": loader.tenant_id, "error": str(exc)},
        )
    return stats


def refrescar_capa_documental(
    loader: GraphLoader,
    settings: Settings,
    snapshot_id: str | None,
    *,
    lector: LectorBlob | None = None,
    tei: TEIEmbeddingClient | None = None,
) -> dict[str, Any]:
    """Paso documental del refresco diario. Aísla sus errores: nunca invalida el grafo de código."""
    control = construir_control_docs(loader, settings)
    resumen: ResumenDocumental | None = None
    try:
        resumen = refrescar_documentos(
            loader,
            lector or LectorBlobHttp(settings.blob_account_url, settings.blob_container),
            tei or TEIEmbeddingClient(base_url=settings.tei_endpoint_url),
            settings.tenant_id,
            control=control,
            tamano_max_caracteres=settings.docs_chunk_max_chars,
            solape_caracteres=settings.docs_chunk_overlap_chars,
            umbral_semantico=settings.docs_link_threshold,
            top_k_semantico=settings.docs_link_top_k,
            tamano_lote_tei=settings.tei_batch_size,
        )
    except Exception as exc:
        logger.error(
            "refresco documental fallido; el grafo de código no se ve afectado",
            extra={"tenant_id": settings.tenant_id, "error": str(exc)},
        )
        if snapshot_id:
            loader.registrar_stats_snapshot(
                snapshot_id, CLAVE_STATS, {"error": f"{type(exc).__name__}: {exc}"[:500]}
            )
        return {"error": str(exc)}
    return registrar_resultado_documental(loader, snapshot_id, control, resumen)
