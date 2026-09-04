"""Exportaciones de auditoría (CSV/PDF) — worker de kc:exports.

CSV: vuelca los eventos filtrados a Blob. PDF de expediente: TODO(produccion)
generación con reportlab/weasyprint; aquí produce un JSON estructurado del
expediente como marcador funcional.
"""
from __future__ import annotations

import csv
import io
import json

from sqlalchemy import text

from kallicode_core.db import sesion_tenant, todos
from kallicode_core.logging import log


async def procesar(campos: dict) -> None:
    export_id, tenant = campos["export_id"], campos["tenant_id"]
    async with sesion_tenant(tenant, actor="svc:exports") as db:
        exp = (await todos(db, "SELECT * FROM core.audit_exports WHERE id=:i",
                           {"i": export_id}))
        if not exp:
            return
        exp = exp[0]
        await db.execute(text("UPDATE core.audit_exports SET estado='procesando' "
                              "WHERE id=:i"), {"i": export_id})
        cond = "tenant_id = :t" + (" AND ticket_id = :tk" if exp["ticket_id"] else "")
        eventos = await todos(db, f"""SELECT creado_en, ticket_id, etapa, actor_tipo,
                                             actor_id, evento, resumen, modelo, sello
                                        FROM audit.audit_events WHERE {cond}
                                        ORDER BY id LIMIT 500000""",
                              {"t": tenant, **({"tk": exp["ticket_id"]}
                                               if exp["ticket_id"] else {})})
        if exp["formato"] == "csv":
            buf = io.StringIO()
            if eventos:
                w = csv.DictWriter(buf, fieldnames=list(eventos[0]))
                w.writeheader()
                w.writerows(eventos)
            contenido = buf.getvalue()
        else:  # pdf -> marcador JSON del expediente (TODO produccion)
            contenido = json.dumps({"expediente": exp["ticket_id"],
                                    "eventos": len(eventos)}, default=str)
        ref = f"{tenant}/exports/{export_id}.{exp['formato']}"
        # TODO(produccion): subir `contenido` a Blob; local solo registra la ref.
        await db.execute(text("""UPDATE core.audit_exports SET estado='lista',
                                        blob_ref=:r, completado_en=now() WHERE id=:i"""),
                         {"r": ref, "i": export_id})
    log.info("audit_export.lista", export_id=export_id, eventos=len(eventos),
             bytes=len(contenido))
