"""Auditoría (§11 del diseño): explorador, expediente, pasos LLM, exports,
integridad de la cadena de hashes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core.auditoria import expediente_ticket, verificar_cadena
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log

from ..deps import UsuarioActual, requiere_rol, sesion_de

router = APIRouter(prefix="/audit", tags=["Auditoría"])

_ROLES_AUDIT = ("owner", "admin", "architect")


class ExportIn(BaseModel):
    formato: str = Field(pattern=r"^(csv|pdf)$")
    ticket_id: str | None = None
    filtros: dict | None = None


@router.get("/events", summary="Explorar eventos de auditoría")
async def eventos(ticket_id: str | None = None, etapa: str | None = None,
                  actor_tipo: str | None = Query(default=None, pattern=r"^(humano|agente|sistema)$"),
                  desde: datetime | None = None, hasta: datetime | None = None,
                  q: str | None = Query(default=None, max_length=200),
                  page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=50, ge=1, le=100),
                  usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Rango máximo 92 días (RANGO_EXCESIVO 422); para más usar exportación.
    El acceso a la auditoría también se audita (log)."""
    if desde and hasta and (hasta - desde).days > 92:
        raise AppError("RANGO_EXCESIVO", 422,
                       "El rango máximo de consulta es de 92 días; usa la exportación "
                       "para períodos mayores.")
    cond, params = ["tenant_id = :t"], {"t": usuario.tenant_id}
    for campo, v in (("ticket_id", ticket_id), ("etapa", etapa),
                     ("actor_tipo", actor_tipo)):
        if v:
            cond.append(f"{campo} = :{campo}"); params[campo] = v
    if desde:
        cond.append("creado_en >= :desde"); params["desde"] = desde
    if hasta:
        cond.append("creado_en <= :hasta"); params["hasta"] = hasta
    if q:
        cond.append("resumen ILIKE :q"); params["q"] = f"%{q}%"
    where = " AND ".join(cond)
    async with sesion_de(usuario) as db:
        items = await todos(db, f"""
            SELECT id, creado_en AS fecha, ticket_id, etapa, actor_tipo, actor_id,
                   resumen AS evento, modelo, sello
              FROM audit.audit_events WHERE {where}
             ORDER BY id DESC LIMIT :lim OFFSET :off""",
            {**params, "lim": page_size, "off": (page - 1) * page_size})
        total = (await uno(db, f"SELECT count(*) AS n FROM audit.audit_events WHERE {where}",
                           params))["n"]
        hoy = (await uno(db, """SELECT count(*) AS n FROM audit.audit_events
                                 WHERE tenant_id=:t AND creado_en > now() - interval '1 day'""",
                         {"t": usuario.tenant_id}))["n"]
    log.info("audit.consultada", filtros=list(params))
    return {"items": items, "cadena": {"eventos_hoy": hoy},
            "total": total, "page": page, "page_size": page_size}


@router.get("/tickets/{numero}", summary="Expediente de auditoría de un ticket")
async def expediente(numero: str,
                     usuario: UsuarioActual = Depends(
                         requiere_rol(*_ROLES_AUDIT, "approver"))) -> dict:
    """Salida: solicitante, integridad, firmas, decisiones por etapa (pasos
    LLM con modelo/tier/confianza/tokens) y eventos completos."""
    async with sesion_de(usuario) as db:
        t = await uno(db, "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                      {"t": usuario.tenant_id, "n": numero})
        if not t:
            raise no_encontrado("El ticket")
        reporter = t["reportado_por"] and await uno(
            db, "SELECT nombre, rol FROM core.users WHERE id=:i", {"i": t["reportado_por"]})
        firmas = await todos(db, """
            SELECT g.gate, g.accion, u.nombre AS actor, g.creado_en AS fecha,
                   g.comentario, g.sello
              FROM core.gate_signatures g JOIN core.users u ON u.id = g.actor_id
             WHERE g.ticket_id=:tk ORDER BY g.creado_en""", {"tk": t["id"]})
        pasos = await todos(db, """
            SELECT etapa, paso, modelo, tier, confianza, validacion,
                   tokens_in + tokens_out AS tokens, duracion_ms, id AS step_id
              FROM core.llm_steps WHERE ticket_id=:tk ORDER BY creado_en""",
            {"tk": t["id"]})
        eventos = await expediente_ticket(db, usuario.tenant_id, numero)
        integridad = await verificar_cadena(db, usuario.tenant_id)
    etapas: dict[str, list] = {}
    for p in pasos:
        etapas.setdefault(p["etapa"], []).append(p)
    log.info("audit.expediente", ticket_id=numero)
    return {"solicitante": {"nombre": reporter and reporter["nombre"],
                            "origen": t["origen"], "fecha": t["creado_en"]},
            "integridad": {"integra": integridad["integra"],
                           "hash_raiz": eventos[-1]["sello"] if eventos else None},
            "firmas": firmas,
            "etapas": [{"etapa": e, "pasos": ps} for e, ps in etapas.items()],
            "eventos": eventos}


@router.get("/steps/{step_id}", summary="Detalle de un paso LLM")
async def paso(step_id: str,
               usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Salida: plantilla+versión, modelo/tier, ejecución (confianza, validación,
    reintentos, tokens) y refs a entrada/salida completas en Blob."""
    async with sesion_de(usuario) as db:
        p = await uno(db, "SELECT * FROM core.llm_steps WHERE id=:i", {"i": step_id})
    if not p:
        raise no_encontrado("El paso")
    log.info("audit.paso", step_id=step_id)
    return {"paso": {"etapa": p["etapa"], "nombre": p["paso"],
                     "plantilla": p["paso"], "version_plantilla": p["version_plantilla"],
                     "modelo": p["modelo"], "tier": p["tier"]},
            "ejecucion": {"confianza": p["confianza"], "validacion": p["validacion"],
                          "reintentos": p["reintentos"], "tokens_in": p["tokens_in"],
                          "tokens_out": p["tokens_out"], "duracion_ms": p["duracion_ms"]},
            "entrada_ref": p["entrada_ref"], "salida_ref": p["salida_ref"]}


@router.post("/exports", status_code=202, summary="Exportar auditoría (CSV/PDF)")
async def exportar(datos: ExportIn,
                   usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Asíncrono: crea la exportación y el worker audit_export la procesa.
    Errores: TICKET_REQUERIDO (422, pdf sin ticket) · EXPORTS_CONCURRENTES (429)."""
    if datos.formato == "pdf" and not datos.ticket_id:
        raise AppError("TICKET_REQUERIDO", 422, "El expediente PDF requiere indicar el ticket.")
    import json
    async with sesion_de(usuario) as db:
        abiertas = (await uno(db, """SELECT count(*) AS n FROM core.audit_exports
                                      WHERE tenant_id=:t AND estado IN ('encolada','procesando')""",
                              {"t": usuario.tenant_id}))["n"]
        if abiertas >= 3:
            raise AppError("EXPORTS_CONCURRENTES", 429,
                           "Ya hay 3 exportaciones en curso; espera a que terminen.")
        eid = nuevo("exp")
        await db.execute(text("""
            INSERT INTO core.audit_exports (id, tenant_id, formato, ticket_id, filtros,
                                            solicitado_por)
            VALUES (:i, :t, :f, :tk, CAST(:fl AS jsonb), :u)"""),
            {"i": eid, "t": usuario.tenant_id, "f": datos.formato,
             "tk": datos.ticket_id, "fl": json.dumps(datos.filtros or {}),
             "u": usuario.user_id})
    from kallicode_core.comercial import redis_cliente
    await redis_cliente().xadd("kc:exports", {"export_id": eid,
                                              "tenant_id": usuario.tenant_id})
    log.info("audit.export.creada", export_id=eid, formato=datos.formato)
    return {"export_id": eid, "estado": "encolada"}


@router.get("/exports/{export_id}", summary="Estado de una exportación")
async def export_estado(export_id: str,
                        usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Salida: estado; con estado='lista' incluye download_url firmada (24 h)."""
    async with sesion_de(usuario) as db:
        e = await uno(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})
    if not e:
        raise no_encontrado("La exportación")
    salida = {"export_id": e["id"], "estado": e["estado"],
              "completado_en": e["completado_en"]}
    if e["estado"] == "lista" and e["blob_ref"]:
        from ..servicios import blob as blobsvc
        url, expira = blobsvc.url_descarga(e["blob_ref"], horas=24)
        salida |= {"download_url": url, "expira_en": expira}
    return salida


@router.get("/integrity", summary="Verificar integridad de la cadena")
async def integridad(ticket_id: str | None = None,
                     desde: datetime | None = None, hasta: datetime | None = None,
                     usuario: UsuarioActual = Depends(requiere_rol(*_ROLES_AUDIT))) -> dict:
    """Recorre la cadena recomputando sellos (audit.verify_chain). Una cadena
    rota emite CRITICAL en el log central y alerta inmediata."""
    d = desde or datetime.now(timezone.utc) - timedelta(days=1)
    async with sesion_de(usuario) as db:
        r = await verificar_cadena(db, usuario.tenant_id, d, hasta)
    log.info("audit.verificacion", integra=r["integra"],
             eventos=r["eventos_verificados"])
    return {"integra": r["integra"], "eventos_verificados": r["eventos_verificados"],
            "primer_evento_roto": r["primer_evento_roto"]}
