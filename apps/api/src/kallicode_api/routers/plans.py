"""Planes: catálogo y solicitud de upgrade (§15.3–15.4 del diseño)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import estado_cuota_loc, plan_vigente
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError
from kallicode_core.ids import nuevo
from kallicode_core.logging import log

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual

router = APIRouter(prefix="/plans", tags=["Consumo y plan"])

_ORDEN = {"starter": 1, "growth": 2, "enterprise": 3}


class UpgradeIn(BaseModel):
    plan_deseado: str = Field(pattern=r"^(starter|growth|enterprise)$")
    comentario: str | None = Field(default=None, max_length=1000)


@router.get("", summary="Planes disponibles")
async def listar(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: catálogo con el plan actual marcado."""
    async with sesion_de(usuario) as db:
        planes = await todos(db, """SELECT codigo, nombre, lineas, loc_mes, despliegues
                                     FROM core.plans ORDER BY 1""")
        actual = await plan_vigente(db, usuario.tenant_id)
    for p in planes:
        p["actual"] = bool(actual and p["codigo"] == actual["codigo"])
    return {"planes": planes}


@router.post("/upgrade-request", status_code=202, summary="Solicitar mejora de plan")
async def upgrade(datos: UpgradeIn,
                  usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Registra la solicitud (el cambio lo aplica un proceso administrativo).

    Errores: PLAN_NO_SUPERIOR (409). Idempotente: una abierta por tenant.
    Comercial: si el tenant está en cuota agotada la solicitud se marca urgente.
    """
    async with sesion_de(usuario) as db:
        actual = await plan_vigente(db, usuario.tenant_id)
        if actual and _ORDEN[datos.plan_deseado] <= _ORDEN[actual["codigo"]]:
            raise AppError("PLAN_NO_SUPERIOR", 409,
                           "El plan solicitado no es superior al actual.")
        abierta = await uno(db, """SELECT id FROM core.upgrade_requests
                                    WHERE tenant_id = :t AND estado IN ('registrada','en_gestion')""",
                            {"t": usuario.tenant_id})
        if abierta:
            return {"solicitud_id": abierta["id"], "estado": "registrada",
                    "mensaje": "Ya hay una solicitud en curso; el equipo comercial te contactará."}
        urgente = (await estado_cuota_loc(db, usuario.tenant_id))["umbral"] == "agotado"
        sid = nuevo("upg")
        await db.execute(text("""
            INSERT INTO core.upgrade_requests (id, tenant_id, plan_deseado, comentario,
                                               urgente, solicitado_por)
            VALUES (:i, :t, :p, :c, :u, :s)"""),
            {"i": sid, "t": usuario.tenant_id, "p": datos.plan_deseado,
             "c": datos.comentario, "u": urgente, "s": usuario.user_id})
        await registrar_evento(db, usuario.tenant_id, evento="configuracion_modificada",
                               resumen=f"Solicitud de upgrade a {datos.plan_deseado}",
                               actor_tipo="humano", actor_id=usuario.user_id,
                               datos={"urgente": urgente})
    log.info("plans.upgrade.solicitado", plan=datos.plan_deseado, urgente=urgente)
    return {"solicitud_id": sid, "estado": "registrada",
            "mensaje": "El equipo comercial te contactará hoy mismo."}
