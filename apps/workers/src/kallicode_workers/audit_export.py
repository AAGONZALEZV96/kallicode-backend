"""Exportaciones de auditoría (CSV/PDF) — worker de kc:exports.

CSV: vuelca los eventos filtrados a Blob. PDF de expediente: TODO(produccion)
generación con reportlab/weasyprint; aquí produce un JSON estructurado del
expediente como marcador funcional.
"""

from __future__ import annotations

import csv
import io
import json

from kallicode_api.servicios.blob import subir_archivo_servidor
from kallicode_core.db import sesion_tenant, todos
from kallicode_core.logging import log
from sqlalchemy import text


async def procesar(campos: dict) -> None:
    export_id, tenant = campos["export_id"], campos["tenant_id"]
    async with sesion_tenant(tenant, actor="svc:exports") as db:
        # CORRECCIÓN: Usamos 'exp_list' para no confundir a mypy
        exp_list = await todos(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})
        if not exp_list:
            return
        exp = exp_list[0]

        await db.execute(
            text("UPDATE core.audit_exports SET estado='procesando' WHERE id=:i"),
            {"i": export_id},
        )
        cond = "tenant_id = :t" + (" AND ticket_id = :tk" if exp["ticket_id"] else "")
        eventos = await todos(
            db,
            f"""SELECT creado_en, ticket_id, etapa, actor_tipo,
                                         actor_id, evento, resumen, modelo, sello
                                    FROM audit.audit_events WHERE {cond}
                                    ORDER BY id LIMIT 500000""",
            {"t": tenant, **({"tk": exp["ticket_id"]} if exp["ticket_id"] else {})},
        )
        if exp["formato"] == "csv":
            buf = io.StringIO()
            if eventos:
                w = csv.DictWriter(buf, fieldnames=list(eventos[0]))
                w.writeheader()
                w.writerows(eventos)
            contenido = buf.getvalue()
        else:  # pdf -> marcador JSON del expediente (TODO produccion)
            contenido = json.dumps(
                {"expediente": exp["ticket_id"], "eventos": len(eventos)}, default=str
            )
        ref = f"{tenant}/exports/{export_id}.{exp['formato']}"
        ########################################################################
        # INICIO CAMBIO KC-BE-05-4: Subida real a Azurite/Blob y control de errores
        contenido_bytes = contenido.encode("utf-8")
        content_type = "text/csv" if exp["formato"] == "csv" else "application/json"

        try:
            # Subimos de forma asíncrona al blob antes de declarar la exportación lista
            await subir_archivo_servidor(
                ref=ref, contenido=contenido_bytes, content_type=content_type
            )
        except Exception as blob_err:
            log.error("audit_export.fallo_subida_blob", export_id=export_id, error=str(blob_err))
            # Si el almacenamiento falla, registramos el error en la BD y abortamos el worker de forma limpia
            await db.execute(
                text(
                    """UPDATE core.audit_exports SET estado='fallida',
                                            error_mensaje=:err, completado_en=now() WHERE id=:i"""
                ),
                {"err": str(blob_err), "i": export_id},
            )
            await db.commit()
            return

        # Si la subida fue exitosa, marcamos como 'lista' y guardamos el ref del blob real
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
            eventos=len(eventos),
            bytes=len(contenido_bytes),
        )
        # FIN CAMBIO KC-BE-05-4
        ########################################################################
