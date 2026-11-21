"""Purgas programadas (§9.3 del doc de BD): tokens expirados, idempotencia
>48 h, adjuntos huérfanos >48 h, trazas LLM expiradas según política de retención
(KC-BE-08-4) y las particiones vencidas de las colas (webhook_inbox, notifications:
DROP de la partición, no DELETE; KC-DB-07-3). Además ejecuta core.validar_integridad()
y alerta si algún chequeo devuelve problemas, y alerta si el dato más antiguo de una
tabla supera su retención declarada en más de 7 días (core.alertas_retencion, KC-DB-10-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from azure.core.exceptions import ResourceNotFoundError
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, todos
from kallicode_core.logging import log
from sqlalchemy import text

from .orquestador.trazas import PREFIJO_TRAZAS_LLM, RETENCION_TRAZAS_LLM_MINIMA_DIAS


async def purgar_trazas_llm_vencidas(
    db,
    ahora: datetime | None = None,
) -> dict[str, int]:
    """Purga trazas LLM en Blob que superaron la política de retención declarada.

    Respeta el piso legal D14 (`RETENCION_TRAZAS_LLM_MINIMA_DIAS = 365` días)
    y lee de `core.retention_policies` las políticas activas de borrado/archivo
    configuradas para `core.llm_steps`.
    """
    ref_tiempo = ahora or datetime.now(UTC)
    politicas = await todos(
        db,
        """
        SELECT tenant_id, retention_days, action
          FROM core.retention_policies
         WHERE tabla = 'core.llm_steps'
           AND action <> 'conservar'
           AND retention_days IS NOT NULL
        """,
    )
    if not politicas:
        return {"tenants_evaluados": 0, "blobs_eliminados": 0}

    settings = get_settings()
    tenants_evaluados = 0
    blobs_eliminados = 0

    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)
        for pol in politicas:
            dias_retencion = max(int(pol["retention_days"]), RETENCION_TRAZAS_LLM_MINIMA_DIAS)
            corte = ref_tiempo - timedelta(days=dias_retencion)
            tenants_evaluados += 1

            cond_tenant = "tenant_id = :t" if pol["tenant_id"] else "true"
            params = {"corte": corte}
            if pol["tenant_id"]:
                params["t"] = pol["tenant_id"]

            pasos_vencidos = await todos(
                db,
                f"""
                SELECT entrada_ref, salida_ref
                  FROM core.llm_steps
                 WHERE {cond_tenant}
                   AND creado_en < :corte
                   AND (entrada_ref LIKE :prefijo OR salida_ref LIKE :prefijo)
                """,
                {**params, "prefijo": f"{PREFIJO_TRAZAS_LLM}/%"},
            )
            for fila in pasos_vencidos:
                for ref in (fila["entrada_ref"], fila["salida_ref"]):
                    if not ref or not ref.startswith(f"{PREFIJO_TRAZAS_LLM}/"):
                        continue
                    try:
                        await contenedor.get_blob_client(ref).delete_blob()
                        blobs_eliminados += 1
                    except ResourceNotFoundError:
                        pass

    return {
        "tenants_evaluados": tenants_evaluados,
        "blobs_eliminados": blobs_eliminados,
    }


async def ciclo() -> None:
    async with sesion_sistema() as db:
        r1 = await db.execute(
            text("DELETE FROM core.refresh_tokens WHERE expira_en < now() - interval '7 days'")
        )
        # KC-DB-10-3: el TTL (48 h) vive en la función SQL; pg_cron la ejecuta cada hora y esta llamada
        # es el respaldo si pg_cron no está disponible.
        r2 = await db.execute(text("SELECT core.purgar_idempotency_keys()"))
        r3 = await db.execute(
            text("""
            DELETE FROM core.ticket_attachments
             WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours'""")
        )
        trazas = await purgar_trazas_llm_vencidas(db)
        # KC-DB-07-3: webhook_inbox y notifications están particionadas por mes; vencer un mes es
        # un DROP de la partición (instantáneo, sin bloat), no un DELETE masivo. La función no
        # elimina una partición con entregas pendientes y devuelve qué hizo con cada una.
        particiones = await todos(
            db,
            "SELECT tabla, particion, filas, accion, motivo FROM core.purgar_particiones_vencidas()",
        )
        problemas = await todos(
            db,
            "SELECT chequeo, problemas FROM core.validar_integridad() WHERE problemas > 0",
        )
        # KC-DB-10-4: se consulta después de purgar, así que una alerta es retención realmente incumplida.
        alertas = await todos(
            db,
            "SELECT tabla, tenant, exceso::text AS exceso FROM core.alertas_retencion()",
        )
    log.info(
        "housekeeping.purgas",
        tokens=r1.rowcount,
        idempotencia=r2.scalar_one(),
        adjuntos=r3.rowcount,
        trazas_llm_blobs=trazas["blobs_eliminados"],
        particiones_eliminadas=sum(1 for p in particiones if p["accion"] == "eliminada"),
        filas_purgadas=sum(p["filas"] for p in particiones if p["accion"] == "eliminada"),
    )
    for p in particiones:
        if p["accion"] == "omitida":
            log.warning(
                "housekeeping.particion_omitida",
                tabla=p["tabla"],
                particion=p["particion"],
                motivo=p["motivo"],
            )
    for p in problemas:
        log.error("housekeeping.integridad", chequeo=p["chequeo"], problemas=p["problemas"])
    for a in alertas:
        log.error(
            "housekeeping.retencion_excedida",
            tabla=a["tabla"],
            tenant=a["tenant"],
            exceso=a["exceso"],
        )
