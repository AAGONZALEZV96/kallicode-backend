"""Auditoría (§11 del diseño): explorador, expediente, pasos LLM, exports,
integridad de la cadena de hashes."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.comercial import redis_cliente
from kallicode_core.config import get_settings
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from kallicode_core.redis_keys import clave_tenant
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from ..deps import UsuarioActual, requiere_rol, sesion_de
from ..paginacion import parse_sort
from ..servicios import blob as blobsvc
from ..servicios.expediente import datos_expediente

_SORT_AUDIT = {
    "id": "id",
    "creado_en": "creado_en",
    "etapa": "etapa",
    "actor_tipo": "actor_tipo",
}

DIAS_MAX_EXPORT_EN_LINEA = 31
LIMITE_PAGINA_EXPORT_EN_LINEA = 100
TTL_PROGRESO_EXPORT_S = 7 * 24 * 3600

router = APIRouter(prefix="/audit", tags=["Auditoría"])

_ROLES_AUDIT = ("owner", "admin", "architect")


def clave_progreso_export(tenant_id: str, export_id: str) -> str:
    return clave_tenant(tenant_id, "export", "progreso", export_id)


async def _guardar_progreso_inicial(tenant_id: str, export_id: str, datos: dict) -> None:
    try:
        await redis_cliente().set(
            clave_progreso_export(tenant_id, export_id),
            json.dumps(datos),
            ex=TTL_PROGRESO_EXPORT_S,
        )
    except Exception as exc:
        log.warning("audit.export.progreso_redis_omitido", export_id=export_id, error=str(exc))


async def _leer_progreso(tenant_id: str, export_id: str, blob_ref: str | None) -> dict:
    try:
        crudo = await redis_cliente().get(clave_progreso_export(tenant_id, export_id))
        if crudo:
            return json.loads(crudo)
    except Exception as exc:
        log.warning("audit.export.lectura_progreso_fallida", export_id=export_id, error=str(exc))

    if blob_ref and blob_ref.endswith(".manifest.json") and not blobsvc._es_remoto():
        try:
            ruta = (Path(get_settings().blob_local_dir).resolve() / blob_ref).resolve()
            if ruta.exists():
                return json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {"eventos_procesados": 0, "partes_generadas": 0}


class FiltrosExport(BaseModel):
    """Filtros del explorador (GET /audit/events) que respeta la exportación (KC-PT-08-3).

    Sin el límite de 92 días del explorador: para períodos largos está la exportación.
    """

    model_config = ConfigDict(extra="forbid")

    actor_tipo: str | None = Field(default=None, pattern=r"^(humano|agente|sistema)$")
    etapa: str | None = Field(default=None, max_length=40)
    desde: datetime | None = None
    hasta: datetime | None = None
    q: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _rango(self) -> FiltrosExport:
        if self.desde and self.hasta and self.hasta < self.desde:
            raise ValueError("«hasta» es anterior a «desde».")
        return self


class ExportIn(BaseModel):
    formato: str = Field(pattern=r"^(csv|pdf)$")
    # Número del ticket (KC-1042), como en audit_events.ticket_id.
    ticket_id: str | None = Field(default=None, max_length=40)
    filtros: FiltrosExport | None = None
    en_linea: bool = False


def _es_rango_largo(filtros: FiltrosExport | None) -> bool:
    if not filtros or not filtros.desde or not filtros.hasta:
        return False
    return (filtros.hasta - filtros.desde) > timedelta(days=DIAS_MAX_EXPORT_EN_LINEA)


@router.get("/events", summary="Explorar eventos de auditoría")
async def eventos(
    ticket_id: str | None = None,
    etapa: str | None = None,
    actor_tipo: str | None = Query(default=None, pattern=r"^(humano|agente|sistema)$"),
    desde: datetime | None = None,
    hasta: datetime | None = None,
    q: str | None = Query(default=None, max_length=200),
    sort: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT)),
) -> dict:
    if desde and hasta and (hasta - desde).days > 92:
        raise AppError(
            "RANGO_EXCESIVO",
            422,
            "El rango máximo de consulta es de 92 días; usa la exportación para períodos mayores.",
        )
    cond, params = ["tenant_id = :t"], {"t": usuario.tenant_id}
    for campo, v in (
        ("ticket_id", ticket_id),
        ("etapa", etapa),
        ("actor_tipo", actor_tipo),
    ):
        if v:
            cond.append(f"{campo} = :{campo}")
            params[campo] = v
    if desde:
        cond.append("creado_en >= :desde")
        params["desde"] = desde
    if hasta:
        cond.append("creado_en <= :hasta")
        params["hasta"] = hasta
    if q:
        cond.append("resumen ILIKE :q")
        params["q"] = f"%{q}%"
    where = " AND ".join(cond)

    columna, ascending = parse_sort(sort, _SORT_AUDIT, default=("id", "desc"))
    direccion = "ASC" if ascending else "DESC"

    async with sesion_de(usuario) as db:
        items = await todos(
            db,
            f"""
            SELECT id, creado_en AS fecha, ticket_id, etapa, actor_tipo, actor_id,
                   resumen AS evento, modelo, sello
              FROM audit.audit_events WHERE {where}
             ORDER BY {columna} {direccion} LIMIT :lim OFFSET :off""",
            {**params, "lim": page_size, "off": (page - 1) * page_size},
        )
        total = (
            await uno(
                db,
                f"SELECT count(*) AS n FROM audit.audit_events WHERE {where}",
                params,
            )
        )["n"]
        hoy = (
            await uno(
                db,
                """SELECT count(*) AS n FROM audit.audit_events
                     WHERE tenant_id=:t AND creado_en > now() - interval '1 day'""",
                {"t": usuario.tenant_id},
            )
        )["n"]
    log.info("audit.consultada", filtros=list(params))
    return {
        "items": items,
        "cadena": {"eventos_hoy": hoy},
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": f"{'-' if not ascending else ''}{columna}",
    }


@router.get("/tickets/{numero}", summary="Expediente de auditoría de un ticket")
async def expediente(
    numero: str,
    usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT, "approver")),
) -> dict:
    """Salida: solicitante, integridad, firmas, decisiones por etapa (pasos
    LLM con modelo/tier/confianza/tokens) y eventos completos."""
    async with sesion_de(usuario) as db:
        datos = await datos_expediente(db, usuario.tenant_id, numero)
    if datos is None:
        raise no_encontrado("El ticket")
    log.info("audit.expediente", ticket_id=numero)
    return datos


@router.get("/steps/{step_id}", summary="Detalle de un paso LLM")
async def paso(step_id: str, usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Salida: plantilla+versión, modelo/tier, ejecución (confianza, validación,
    reintentos, tokens) y refs a entrada/salida completas en Blob."""
    async with sesion_de(usuario) as db:
        p = await uno(db, "SELECT * FROM core.llm_steps WHERE id=:i", {"i": step_id})
    if not p:
        raise no_encontrado("El paso")
    log.info("audit.paso", step_id=step_id)
    return {
        "paso": {
            "etapa": p["etapa"],
            "nombre": p["paso"],
            "plantilla": p["paso"],
            "version_plantilla": p["version_plantilla"],
            "modelo": p["modelo"],
            "tier": p["tier"],
        },
        "ejecucion": {
            "confianza": p["confianza"],
            "validacion": p["validacion"],
            "reintentos": p["reintentos"],
            "tokens_in": p["tokens_in"],
            "tokens_out": p["tokens_out"],
            "duracion_ms": p["duracion_ms"],
        },
        "entrada_ref": p["entrada_ref"],
        "salida_ref": p["salida_ref"],
    }


@router.post("/exports", status_code=202, summary="Exportar auditoría (CSV/PDF)")
async def exportar(datos: ExportIn, usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))):
    """Asíncrono (202): crea la exportación y el worker audit_export la procesa.
    Los rangos superiores a 31 días exigen el camino asíncrono (202); los inferiores
    pueden resolverse en línea con `en_linea=true` hasta el límite de página (100).
    Errores: TICKET_REQUERIDO (422, pdf sin ticket) · RANGO_EXCESIVO (422, en_linea > 31d) ·
    RECURSO_NO_ENCONTRADO (404, ticket) · EXPORTS_CONCURRENTES (429)."""
    if datos.formato == "pdf" and not datos.ticket_id:
        raise AppError("TICKET_REQUERIDO", 422, "El expediente PDF requiere indicar el ticket.")

    rango_largo = _es_rango_largo(datos.filtros)
    if datos.en_linea and rango_largo:
        raise AppError(
            "RANGO_EXCESIVO",
            422,
            "Los rangos superiores a 31 días exigen exportación asíncrona.",
        )

    async with sesion_de(usuario) as db:
        abiertas = (
            await uno(
                db,
                """SELECT count(*) AS n FROM core.audit_exports
                                      WHERE tenant_id=:t AND estado IN ('encolada','procesando')""",
                {"t": usuario.tenant_id},
            )
        )["n"]
        if abiertas >= 3:
            raise AppError(
                "EXPORTS_CONCURRENTES",
                429,
                "Ya hay 3 exportaciones en curso; espera a que terminen.",
            )

        ticket_interno = None
        if datos.ticket_id:
            ticket = await uno(
                db,
                "SELECT id FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                {"t": usuario.tenant_id, "n": datos.ticket_id},
            )
            if not ticket:
                raise no_encontrado("El ticket")
            ticket_interno = ticket["id"]

        eid = nuevo("exp")
        filtros_json = (
            datos.filtros.model_dump(mode="json", exclude_none=True) if datos.filtros else {}
        )

        # Resolución en línea opcional para rangos <= 31 días en CSV
        if datos.en_linea and datos.formato == "csv" and not rango_largo:
            cond, params = ["tenant_id = :t"], {"t": usuario.tenant_id}
            if datos.ticket_id:
                cond.append("ticket_id = :tk")
                params["tk"] = datos.ticket_id
            if datos.filtros:
                if datos.filtros.actor_tipo:
                    cond.append("actor_tipo = :actor_tipo")
                    params["actor_tipo"] = datos.filtros.actor_tipo
                if datos.filtros.etapa:
                    cond.append("etapa = :etapa")
                    params["etapa"] = datos.filtros.etapa
                if datos.filtros.desde:
                    cond.append("creado_en >= :desde")
                    params["desde"] = datos.filtros.desde
                if datos.filtros.hasta:
                    cond.append("creado_en <= :hasta")
                    params["hasta"] = datos.filtros.hasta
                if datos.filtros.q:
                    cond.append("resumen ILIKE :q")
                    params["q"] = f"%{datos.filtros.q}%"
            where = " AND ".join(cond)
            filas = await todos(
                db,
                f"""SELECT creado_en, ticket_id, etapa, actor_tipo, actor_id, evento,
                           resumen, modelo, sello
                      FROM audit.audit_events
                     WHERE {where}
                     ORDER BY id ASC
                     LIMIT :lim""",
                {**params, "lim": LIMITE_PAGINA_EXPORT_EN_LINEA},
            )
            buf = io.StringIO()
            if filas:
                w = csv.DictWriter(buf, fieldnames=list(filas[0]))
                w.writeheader()
                w.writerows(filas)
            contenido = buf.getvalue().encode("utf-8")
            ref = f"{usuario.tenant_id}/exports/{eid}.csv"
            await blobsvc.subir_archivo_servidor(
                ref=ref, contenido=contenido, content_type="text/csv"
            )
            await db.execute(
                text("""
                INSERT INTO core.audit_exports (id, tenant_id, formato, ticket_id, filtros,
                                                estado, blob_ref, solicitado_por, completado_en)
                VALUES (:i, :t, 'csv', :tk, CAST(:fl AS jsonb), 'lista', :r, :u, now())"""),
                {
                    "i": eid,
                    "t": usuario.tenant_id,
                    "tk": ticket_interno,
                    "fl": json.dumps(filtros_json),
                    "r": ref,
                    "u": usuario.user_id,
                },
            )
            progreso_linea = {
                "estado": "lista",
                "eventos_procesados": len(filas),
                "partes_generadas": 1,
                "partes": [
                    {
                        "parte": 1,
                        "ref": ref,
                        "eventos": len(filas),
                        "sha256": hashlib.sha256(contenido).hexdigest(),
                    }
                ],
            }
            await _guardar_progreso_inicial(usuario.tenant_id, eid, progreso_linea)
            url, expira = await asyncio.to_thread(blobsvc.url_descarga, ref, horas=24)
            items_serializados = [
                {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fila.items()}
                for fila in filas
            ]
            return JSONResponse(
                status_code=200,
                content={
                    "export_id": eid,
                    "estado": "lista",
                    "eventos_procesados": len(filas),
                    "partes_generadas": 1,
                    "download_url": url,
                    "expira_en": expira.isoformat(),
                    "items": items_serializados,
                },
            )

        await db.execute(
            text("""
            INSERT INTO core.audit_exports (id, tenant_id, formato, ticket_id, filtros,
                                            solicitado_por)
            VALUES (:i, :t, :f, :tk, CAST(:fl AS jsonb), :u)"""),
            {
                "i": eid,
                "t": usuario.tenant_id,
                "f": datos.formato,
                "tk": ticket_interno,
                "fl": json.dumps(filtros_json),
                "u": usuario.user_id,
            },
        )

    await _guardar_progreso_inicial(
        usuario.tenant_id,
        eid,
        {
            "estado": "encolada",
            "eventos_procesados": 0,
            "partes_generadas": 0,
            "partes": [],
        },
    )

    await redis_cliente().xadd(
        "kc:exports",
        {"export_id": eid, "tenant_id": usuario.tenant_id},
        maxlen=10000,
        approximate=True,
    )
    log.info("audit.export.creada", export_id=eid, formato=datos.formato, rango_largo=rango_largo)
    return {
        "export_id": eid,
        "estado": "encolada",
        "eventos_procesados": 0,
        "partes_generadas": 0,
    }


@router.get("/exports/{export_id}", summary="Estado de una exportación")
async def export_estado(
    export_id: str, usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))
) -> dict:
    """Salida: estado, eventos_procesados y partes_generadas; con estado='lista' incluye download_url firmada (24 h)."""
    async with sesion_de(usuario) as db:
        e = await uno(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})
    if not e:
        raise no_encontrado("La exportación")

    progreso = await _leer_progreso(usuario.tenant_id, export_id, e["blob_ref"])
    salida: dict = {
        "export_id": e["id"],
        "estado": e["estado"],
        "eventos_procesados": int(progreso.get("eventos_procesados", 0)),
        "partes_generadas": int(progreso.get("partes_generadas", 0)),
        "completado_en": e["completado_en"],
    }
    if e["estado"] == "lista" and e["blob_ref"]:
        url, expira = await asyncio.to_thread(
            blobsvc.url_descarga,
            e["blob_ref"],
            horas=24,
        )
        salida |= {"download_url": url, "expira_en": expira}

        partes_crudo = progreso.get("partes") or []
        if partes_crudo:
            partes_salida = []
            for p in partes_crudo:
                url_parte, _ = await asyncio.to_thread(blobsvc.url_descarga, p["ref"], horas=24)
                partes_salida.append({**p, "download_url": url_parte})
            salida["partes"] = partes_salida
        if progreso.get("manifiesto_ref"):
            salida["manifiesto_ref"] = progreso["manifiesto_ref"]
    return salida


@router.get("/integrity", summary="Verificar integridad de la cadena")
async def integridad(
    ticket_id: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT)),
) -> dict:
    """Recorre la cadena recomputando sellos (audit.verify_chain o filtrado por ticket_id)."""
    d = desde or datetime.now(UTC) - timedelta(days=1)
    async with sesion_de(usuario) as db:
        r = await verificar_cadena(db, usuario.tenant_id, d, hasta, ticket_id=ticket_id)
    log.info(
        "audit.verificacion",
        ticket_id=ticket_id,
        integra=r["integra"],
        eventos=r["eventos_verificados"],
    )
    return {
        "integra": r["integra"],
        "eventos_verificados": r["eventos_verificados"],
        "primer_evento_roto": r["primer_evento_roto"],
    }
