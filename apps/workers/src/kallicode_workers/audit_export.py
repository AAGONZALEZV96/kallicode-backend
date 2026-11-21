"""Exportaciones de auditoría (CSV/PDF) — worker de kc:exports (KC-BE-08-1).

CSV: vuelca a Blob los eventos del ticket o del tenant con los filtros del explorador
(audit.FiltrosExport), troceando en partes de hasta 500.000 eventos con manifiesto y
reporte incremental de progreso (eventos_procesados y partes_generadas).
PDF: el expediente del ticket (servicios.expediente), generado con reportlab.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, datetime

from kallicode_api.servicios.blob import subir_archivo_servidor
from kallicode_api.servicios.expediente import datos_expediente
from kallicode_core.comercial import redis_cliente
from kallicode_core.db import sesion_tenant, todos, uno
from kallicode_core.logging import log
from kallicode_core.redis_keys import clave_tenant
from sqlalchemy import text

from .expediente_pdf import generar_pdf

TAMANO_PARTE_EVENTOS = 500_000
LOTE_LECTURA_EVENTOS = 50_000
TTL_PROGRESO_EXPORT_S = 7 * 24 * 3600

_CONDICIONES = {
    "actor_tipo": "actor_tipo = :actor_tipo",
    "etapa": "etapa = :etapa",
    "desde": "creado_en >= :desde",
    "hasta": "creado_en <= :hasta",
    "q": "resumen ILIKE :q",
}
_CONTENT_TYPE = {"csv": "text/csv", "pdf": "application/pdf"}


def clave_progreso_export(tenant_id: str, export_id: str) -> str:
    return clave_tenant(tenant_id, "export", "progreso", export_id)


async def _actualizar_progreso(tenant_id: str, export_id: str, estado_progreso: dict) -> None:
    try:
        await redis_cliente().set(
            clave_progreso_export(tenant_id, export_id),
            json.dumps(estado_progreso),
            ex=TTL_PROGRESO_EXPORT_S,
        )
    except Exception as exc:
        log.warning("audit_export.progreso_redis_omitido", export_id=export_id, error=str(exc))


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
            valor = datetime.fromisoformat(valor)
        params[clave] = valor
    return " AND ".join(condiciones), params


def _serializar_csv_parte(filas: list[dict], columnas: list[str]) -> bytes:
    if not filas:
        return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columnas, extrasaction="ignore")
    w.writeheader()
    w.writerows(filas)
    return buf.getvalue().encode("utf-8")


def construir_manifiesto(
    *,
    export_id: str,
    tenant_id: str,
    formato: str,
    ticket_numero: str | None,
    filtros: dict | None,
    total_eventos: int,
    partes: list[dict],
    generado_en: datetime,
) -> dict:
    return {
        "export_id": export_id,
        "tenant_id": tenant_id,
        "formato": formato,
        "ticket_id": ticket_numero,
        "filtros": filtros or {},
        "total_eventos": total_eventos,
        "eventos_procesados": total_eventos,
        "partes_generadas": len(partes),
        "tamano_maximo_parte": TAMANO_PARTE_EVENTOS,
        "partes": partes,
        "generado_en": generado_en.isoformat(),
    }


async def _marcar_fallida(db, tenant_id: str, export_id: str, progreso: dict) -> None:
    await db.execute(
        text("UPDATE core.audit_exports SET estado='fallida', completado_en=now() WHERE id=:i"),
        {"i": export_id},
    )
    if db.in_transaction():
        await db.commit()
    await _actualizar_progreso(tenant_id, export_id, {**progreso, "estado": "fallida"})


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
        if db.in_transaction():
            await db.commit()

    async with sesion_tenant(tenant, actor="svc:exports") as db:
        progreso: dict = {
            "estado": "procesando",
            "eventos_procesados": 0,
            "partes_generadas": 0,
            "partes": [],
        }
        await _actualizar_progreso(tenant, export_id, progreso)

        numero = None
        if exp["ticket_id"]:
            ticket = await uno(
                db, "SELECT numero FROM core.tickets WHERE id=:i", {"i": exp["ticket_id"]}
            )
            numero = ticket and ticket["numero"]

        if exp["formato"] == "csv":
            where, params = filtro_eventos(tenant, numero, exp["filtros"])
            columnas = [
                "creado_en",
                "ticket_id",
                "etapa",
                "actor_tipo",
                "actor_id",
                "evento",
                "resumen",
                "modelo",
                "sello",
            ]
            ultimo_id = 0
            buffer_parte: list[dict] = []
            partes: list[dict] = []
            eventos_procesados = 0
            bytes_totales = 0

            async def _volcar_parte(filas_parte: list[dict], indice_parte: int) -> None:
                nonlocal bytes_totales
                contenido_parte = _serializar_csv_parte(filas_parte, columnas)
                bytes_totales += len(contenido_parte)
                ref_parte = (
                    f"{tenant}/exports/{export_id}.csv"
                    if indice_parte == 1
                    else f"{tenant}/exports/{export_id}.part-{indice_parte:04d}.csv"
                )
                await subir_archivo_servidor(
                    ref=ref_parte,
                    contenido=contenido_parte,
                    content_type=_CONTENT_TYPE["csv"],
                )
                partes.append(
                    {
                        "parte": indice_parte,
                        "ref": ref_parte,
                        "eventos": len(filas_parte),
                        "sha256": hashlib.sha256(contenido_parte).hexdigest(),
                    }
                )
                progreso["eventos_procesados"] = eventos_procesados
                progreso["partes_generadas"] = len(partes)
                progreso["partes"] = partes
                await _actualizar_progreso(tenant, export_id, progreso)

            try:
                while True:
                    lote = await todos(
                        db,
                        f"""SELECT id, creado_en, ticket_id, etapa, actor_tipo, actor_id, evento,
                                   resumen, modelo, sello
                              FROM audit.audit_events
                             WHERE {where} AND id > :ultimo_id
                             ORDER BY id ASC
                             LIMIT :lote""",
                        {**params, "ultimo_id": ultimo_id, "lote": LOTE_LECTURA_EVENTOS},
                    )
                    if not lote:
                        break

                    ultimo_id = lote[-1]["id"]
                    for fila in lote:
                        buffer_parte.append(fila)
                        eventos_procesados += 1
                        if len(buffer_parte) >= TAMANO_PARTE_EVENTOS:
                            await _volcar_parte(buffer_parte, len(partes) + 1)
                            buffer_parte = []

                    progreso["eventos_procesados"] = eventos_procesados
                    progreso["partes_generadas"] = len(partes)
                    await _actualizar_progreso(tenant, export_id, progreso)

                if buffer_parte or not partes:
                    await _volcar_parte(buffer_parte, len(partes) + 1)

                manifiesto_ref = f"{tenant}/exports/{export_id}.manifest.json"
                manifiesto = construir_manifiesto(
                    export_id=export_id,
                    tenant_id=tenant,
                    formato="csv",
                    ticket_numero=numero,
                    filtros=exp["filtros"],
                    total_eventos=eventos_procesados,
                    partes=partes,
                    generado_en=datetime.now(UTC),
                )
                await subir_archivo_servidor(
                    ref=manifiesto_ref,
                    contenido=json.dumps(manifiesto, ensure_ascii=False, indent=2).encode("utf-8"),
                    content_type="application/json",
                )
            except Exception as blob_err:
                log.error(
                    "audit_export.fallo_subida_blob", export_id=export_id, error=str(blob_err)
                )
                await _marcar_fallida(db, tenant, export_id, progreso)
                return

            ref_principal = manifiesto_ref if len(partes) > 1 else partes[0]["ref"]
            progreso_final = {
                "estado": "lista",
                "eventos_procesados": eventos_procesados,
                "partes_generadas": len(partes),
                "manifiesto_ref": manifiesto_ref,
                "partes": partes,
            }
            await db.execute(
                text(
                    """UPDATE core.audit_exports SET estado='lista',
                                            blob_ref=:r, completado_en=now() WHERE id=:i"""
                ),
                {"r": ref_principal, "i": export_id},
            )
            await db.commit()
            await _actualizar_progreso(tenant, export_id, progreso_final)

            log.info(
                "audit_export.lista",
                export_id=export_id,
                formato="csv",
                eventos=eventos_procesados,
                partes=len(partes),
                bytes=bytes_totales,
            )
            return

        datos = numero and await datos_expediente(db, tenant, numero)
        if not datos:
            log.warning("audit_export.ticket_inexistente", export_id=export_id)
            await _marcar_fallida(db, tenant, export_id, progreso)
            return
        contenido = generar_pdf(numero, datos, generado_en=datetime.now(UTC), export_id=export_id)
        cantidad = len(datos["eventos"])
        ref = f"{tenant}/exports/{export_id}.pdf"
        try:
            await subir_archivo_servidor(
                ref=ref, contenido=contenido, content_type=_CONTENT_TYPE["pdf"]
            )
        except Exception as blob_err:
            log.error("audit_export.fallo_subida_blob", export_id=export_id, error=str(blob_err))
            await _marcar_fallida(db, tenant, export_id, progreso)
            return

        partes_pdf = [
            {
                "parte": 1,
                "ref": ref,
                "eventos": cantidad,
                "sha256": hashlib.sha256(contenido).hexdigest(),
            }
        ]
        await db.execute(
            text(
                """UPDATE core.audit_exports SET estado='lista',
                                        blob_ref=:r, completado_en=now() WHERE id=:i"""
            ),
            {"r": ref, "i": export_id},
        )
        await db.commit()
        await _actualizar_progreso(
            tenant,
            export_id,
            {
                "estado": "lista",
                "eventos_procesados": cantidad,
                "partes_generadas": 1,
                "partes": partes_pdf,
            },
        )

        log.info(
            "audit_export.lista",
            export_id=export_id,
            formato="pdf",
            eventos=cantidad,
            bytes=len(contenido),
        )
