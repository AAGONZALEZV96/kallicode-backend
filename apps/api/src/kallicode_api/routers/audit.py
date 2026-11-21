"""Auditoría (§11 del diseño): explorador, expediente, pasos LLM, exports,
integridad de la cadena de hashes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from ..deps import UsuarioActual, requiere_rol, sesion_de
from ..paginacion import parse_sort
from ..servicios.expediente import datos_expediente

_SORT_AUDIT = {
    "id": "id",
    "creado_en": "creado_en",
    "etapa": "etapa",
    "actor_tipo": "actor_tipo",
}

router = APIRouter(prefix="/audit", tags=["Auditoría"])

_ROLES_AUDIT = ("owner", "admin", "architect")


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
async def exportar(
    datos: ExportIn, usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))
) -> dict:
    """Asíncrono: crea la exportación y el worker audit_export la procesa. CSV: los eventos
    del ticket o del tenant con los filtros del explorador. PDF: el expediente del ticket.
    `ticket_id` es el número del ticket (KC-1042), como en el explorador.
    Errores: TICKET_REQUERIDO (422, pdf sin ticket) · RECURSO_NO_ENCONTRADO (404, ticket) ·
    EXPORTS_CONCURRENTES (429)."""
    if datos.formato == "pdf" and not datos.ticket_id:
        raise AppError("TICKET_REQUERIDO", 422, "El expediente PDF requiere indicar el ticket.")
    import json

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
        # audit_exports.ticket_id apunta a core.tickets(id); los eventos guardan el número.
        # Antes se insertaba lo recibido: con el número fallaba la FK (500) y con el id el
        # worker no encontraba eventos (KC-PT-08-3).
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
                "fl": json.dumps(
                    datos.filtros.model_dump(mode="json", exclude_none=True)
                    if datos.filtros
                    else {}
                ),
                "u": usuario.user_id,
            },
        )
    from kallicode_core.comercial import redis_cliente

    await redis_cliente().xadd(
        "kc:exports",
        {"export_id": eid, "tenant_id": usuario.tenant_id},
        maxlen=10000,
        approximate=True,
    )
    log.info("audit.export.creada", export_id=eid, formato=datos.formato)
    return {"export_id": eid, "estado": "encolada"}


@router.get("/exports/{export_id}", summary="Estado de una exportación")
async def export_estado(
    export_id: str, usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))
) -> dict:
    """Salida: estado; con estado='lista' incluye download_url firmada (24 h)."""
    async with sesion_de(usuario) as db:
        e = await uno(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})
    if not e:
        raise no_encontrado("La exportación")
    salida = {
        "export_id": e["id"],
        "estado": e["estado"],
        "completado_en": e["completado_en"],
    }
    if e["estado"] == "lista" and e["blob_ref"]:
        from ..servicios import blob as blobsvc

        url, expira = await asyncio.to_thread(
            blobsvc.url_descarga,
            e["blob_ref"],
            horas=24,
        )

        salida |= {"download_url": url, "expira_en": expira}
    return salida


@router.get("/integrity", summary="Verificar integridad de la cadena")
async def integridad(
    ticket_id: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT)),
) -> dict:
    """Recorre la cadena recomputando sellos (audit.verify_chain). Una cadena
    rota emite CRITICAL en el log central y alerta inmediata."""
    d = desde or datetime.now(UTC) - timedelta(days=1)
    async with sesion_de(usuario) as db:
        r = await verificar_cadena(db, usuario.tenant_id, d, hasta)
    log.info("audit.verificacion", integra=r["integra"], eventos=r["eventos_verificados"])
    return {
        "integra": r["integra"],
        "eventos_verificados": r["eventos_verificados"],
        "primer_evento_roto": r["primer_evento_roto"],
    }
