"""Exportaciones de auditoría (CSV/PDF) — worker de kc:exports.

CSV: vuelca a Blob los eventos del ticket o del tenant con los filtros del explorador
(audit.FiltrosExport: actor, etapa, rango de fechas y texto; KC-PT-08-3).
PDF: el expediente del ticket (servicios.expediente), generado con reportlab
(expediente_pdf); antes era un JSON de marcador con extensión .pdf.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from kallicode_api.servicios.blob import subir_archivo_servidor
from kallicode_api.servicios.expediente import datos_expediente
from kallicode_core.db import sesion_tenant, todos, uno
from kallicode_core.logging import log
from sqlalchemy import text

from .expediente_pdf import generar_pdf

# Filtro → condición SQL fija; el valor siempre va como parámetro. Una clave desconocida se
# ignora (la API ya las rechaza con FiltrosExport).
_CONDICIONES = {
    "actor_tipo": "actor_tipo = :actor_tipo",
    "etapa": "etapa = :etapa",
    "desde": "creado_en >= :desde",
    "hasta": "creado_en <= :hasta",
    "q": "resumen ILIKE :q",
}
_CONTENT_TYPE = {"csv": "text/csv", "pdf": "application/pdf"}


def filtro_eventos(tenant: str, ticket: str | None, filtros: dict | None) -> tuple[str, dict]:
    """WHERE y parámetros de los eventos a exportar: los mismos filtros que GET /audit/events."""
    condiciones, params = ["tenant_id = :t"], {"t": tenant}
    if ticket:
        condiciones.append("ticket_id = :tk")
        params["tk"] = ticket
    for clave, condicion in _CONDICIONES.items():
        valor = (filtros or {}).get(clave)
        if valor in (None, ""):
            continue
        condiciones.append(condicion)
        if clave == "q":
            valor = f"%{valor}%"
        elif clave in ("desde", "hasta"):
            # En filtros (jsonb) las fechas quedan como texto ISO; asyncpg exige datetime.
            valor = datetime.fromisoformat(valor)
        params[clave] = valor
    return " AND ".join(condiciones), params


async def _marcar_fallida(db, export_id: str) -> None:
    await db.execute(
        text("UPDATE core.audit_exports SET estado='fallida', completado_en=now() WHERE id=:i"),
        {"i": export_id},
    )
    await db.commit()


async def procesar(campos: dict) -> None:
    export_id, tenant = campos["export_id"], campos["tenant_id"]
    async with sesion_tenant(tenant, actor="svc:exports") as db:
        exp_list = await todos(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})
        if not exp_list:
            return
        exp = exp_list[0]

        await db.execute(
            text("UPDATE core.audit_exports SET estado='procesando' WHERE id=:i"),
            {"i": export_id},
        )

        # audit_exports.ticket_id es el id interno; los eventos y el expediente usan el número.
        numero = None
        if exp["ticket_id"]:
            ticket = await uno(
                db, "SELECT numero FROM core.tickets WHERE id=:i", {"i": exp["ticket_id"]}
            )
            numero = ticket and ticket["numero"]

        if exp["formato"] == "csv":
            where, params = filtro_eventos(tenant, numero, exp["filtros"])
            eventos = await todos(
                db,
                f"""SELECT creado_en, ticket_id, etapa, actor_tipo, actor_id, evento,
                           resumen, modelo, sello
                      FROM audit.audit_events WHERE {where}
                     ORDER BY id LIMIT 500000""",
                params,
            )
            buf = io.StringIO()
            if eventos:
                w = csv.DictWriter(buf, fieldnames=list(eventos[0]))
                w.writeheader()
                w.writerows(eventos)
            contenido = buf.getvalue().encode("utf-8")
            cantidad = len(eventos)
        else:
            datos = numero and await datos_expediente(db, tenant, numero)
            if not datos:
                log.warning("audit_export.ticket_inexistente", export_id=export_id)
                await _marcar_fallida(db, export_id)
                return
            contenido = generar_pdf(
                numero, datos, generado_en=datetime.now(UTC), export_id=export_id
            )
            cantidad = len(datos["eventos"])

        ref = f"{tenant}/exports/{export_id}.{exp['formato']}"
        try:
            # Se sube al Blob antes de declarar la exportación lista (KC-BE-05-4).
            await subir_archivo_servidor(
                ref=ref, contenido=contenido, content_type=_CONTENT_TYPE[exp["formato"]]
            )
        except Exception as blob_err:
            # El detalle del error queda en el log; la tabla solo registra el estado.
            log.error("audit_export.fallo_subida_blob", export_id=export_id, error=str(blob_err))
            await _marcar_fallida(db, export_id)
            return

        await db.execute(
            text(
                """UPDATE core.audit_exports SET estado='lista',
                                        blob_ref=:r, completado_en=now() WHERE id=:i"""
            ),
            {"r": ref, "i": export_id},
        )
        await db.commit()

        log.info(
            "audit_export.lista",
            export_id=export_id,
            formato=exp["formato"],
            eventos=cantidad,
            bytes=len(contenido),
        )
