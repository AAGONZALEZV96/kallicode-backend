"""Planes: catálogo y solicitud de upgrade (§15.3–15.4 del diseño)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import (
    aplicar_plan,
    bloquear_suscripcion,
    estado_cuota_loc,
    plan_vigente,
)
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..deps import (
    ServicioActual,
    UsuarioActual,
    requiere_rol,
    servicio_comercial,
    sesion_de,
    usuario_actual,
)

router = APIRouter(prefix="/plans", tags=["Consumo y plan"])
router_interno = APIRouter(prefix="/plans", tags=["Gestión comercial"])

_ORDEN = {"starter": 1, "growth": 2, "enterprise": 3}
_LIMITES = ("loc_mes", "lineas", "max_usuarios", "storage_mb")


class UpgradeIn(BaseModel):
    plan_deseado: str = Field(pattern=r"^(starter|growth|enterprise)$")
    comentario: str | None = Field(default=None, max_length=1000)


class LimitesPlan(BaseModel):
    loc_mes: int | None
    lineas: int | None
    max_usuarios: int | None
    storage_mb: int | None


class PlanComparable(LimitesPlan):
    codigo: str
    nombre: str
    despliegues: list[str]
    actual: bool
    delta: LimitesPlan = Field(
        description="Plan menos vigente. Null para límites a medida o sin plan vigente."
    )


class ComparadorPlanes(BaseModel):
    planes: list[PlanComparable]


class SolicitudMejora(BaseModel):
    solicitud_id: str
    plan_deseado: str
    estado: Literal["registrada", "en_gestion", "aplicada", "descartada"]
    urgente: bool
    creada_en: datetime


@router.get("", summary="Planes disponibles")
async def listar(usuario: UsuarioActual = Depends(usuario_actual)) -> ComparadorPlanes:
    """Catálogo con el plan vigente y las diferencias de capacidad."""
    async with sesion_de(usuario) as db:
        planes = await todos(
            db,
            """SELECT codigo, nombre, lineas, loc_mes, max_usuarios, storage_mb, despliegues
                 FROM core.plans ORDER BY codigo""",
        )
        actual = await plan_vigente(db, usuario.tenant_id)
    for p in planes:
        p["actual"] = bool(actual and p["codigo"] == actual["codigo"])
        p["delta"] = {
            limite: p[limite] - actual[limite]
            if actual and actual[limite] is not None and p[limite] is not None
            else None
            for limite in _LIMITES
        }
    return ComparadorPlanes.model_validate({"planes": planes})


@router.get("/upgrade-request", summary="Consultar la última solicitud de mejora")
async def consultar(
    usuario: UsuarioActual = Depends(requiere_rol("owner", "admin")),
) -> SolicitudMejora | None:
    async with sesion_de(usuario) as db:
        fila = await uno(
            db,
            """SELECT id AS solicitud_id, plan_deseado, estado, urgente, creado_en AS creada_en
                 FROM core.upgrade_requests
                WHERE tenant_id = :t ORDER BY creado_en DESC, id DESC LIMIT 1""",
            {"t": usuario.tenant_id},
        )
    return SolicitudMejora.model_validate(fila) if fila else None


@router.post("/upgrade-request", status_code=202, summary="Solicitar mejora de plan")
async def upgrade(
    datos: UpgradeIn, usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))
) -> dict:
    """Registra la solicitud (el cambio lo aplica un proceso administrativo).

    Errores: PLAN_NO_SUPERIOR (409). Idempotente: una abierta por tenant.
    Comercial: si el tenant está en cuota agotada la solicitud se marca urgente.
    """
    async with sesion_de(usuario) as db:
        actual = await bloquear_suscripcion(db, usuario.tenant_id)
        orden_actual = _ORDEN.get(actual["plan_codigo"]) if actual else None
        if orden_actual is None or _ORDEN[datos.plan_deseado] <= orden_actual:
            raise AppError(
                "PLAN_NO_SUPERIOR", 409, "El plan solicitado requiere una suscripción inferior."
            )
        abierta = await uno(
            db,
            """SELECT id, estado FROM core.upgrade_requests
                                    WHERE tenant_id = :t AND estado IN ('registrada','en_gestion')""",
            {"t": usuario.tenant_id},
        )
        if abierta:
            return {
                "solicitud_id": abierta["id"],
                "estado": abierta["estado"],
                "mensaje": "Ya hay una solicitud en curso; el equipo comercial te contactará.",
            }
        urgente = (await estado_cuota_loc(db, usuario.tenant_id))["umbral"] == "agotado"
        sid = nuevo("upg")
        await db.execute(
            text("""
            INSERT INTO core.upgrade_requests (id, tenant_id, plan_deseado, comentario,
                                               urgente, solicitado_por)
            VALUES (:i, :t, :p, :c, :u, :s)"""),
            {
                "i": sid,
                "t": usuario.tenant_id,
                "p": datos.plan_deseado,
                "c": datos.comentario,
                "u": urgente,
                "s": usuario.user_id,
            },
        )
        await registrar_evento(
            db,
            usuario.tenant_id,
            evento="mejora_plan_solicitada",
            resumen=f"Solicitud de mejora a {datos.plan_deseado}",
            actor_tipo="humano",
            actor_id=usuario.user_id,
            datos={"solicitud_id": sid, "plan_deseado": datos.plan_deseado, "urgente": urgente},
        )
    log.info("plans.upgrade.solicitado", plan=datos.plan_deseado, urgente=urgente)
    return {
        "solicitud_id": sid,
        "estado": "registrada",
        "mensaje": "El equipo comercial revisará tu solicitud.",
    }


@router_interno.post(
    "/upgrade-requests/{solicitud_id}/approve", summary="Aprobar una solicitud de mejora"
)
async def aprobar(
    solicitud_id: str, svc: ServicioActual = Depends(servicio_comercial)
) -> SolicitudMejora:
    tenant = svc.tenant_id
    async with sesion_de(svc) as db:
        suscripcion = await bloquear_suscripcion(db, tenant)
        solicitud = await uno(
            db,
            """
            SELECT r.id AS solicitud_id, r.plan_deseado, r.estado, r.urgente,
                   r.creado_en AS creada_en, p.lineas
              FROM core.upgrade_requests r
              JOIN core.plans p ON p.codigo = r.plan_deseado
             WHERE r.tenant_id = :t AND r.id = :id
             FOR UPDATE OF r
            """,
            {"t": tenant, "id": solicitud_id},
        )
        if not solicitud:
            raise no_encontrado("La solicitud")
        if solicitud["estado"] == "aplicada":
            return SolicitudMejora.model_validate(solicitud)
        codigo = solicitud["plan_deseado"]
        if (
            solicitud["estado"] == "descartada"
            or not suscripcion
            or _ORDEN.get(codigo, 0) < _ORDEN.get(suscripcion["plan_codigo"], len(_ORDEN) + 1)
        ):
            raise AppError("TRANSICION_INVALIDA", 409, "La solicitud ya no puede aplicarse.")

        await aplicar_plan(db, tenant, suscripcion["id"], codigo, solicitud["lineas"])
        await db.execute(
            text(
                "UPDATE core.upgrade_requests SET estado = 'aplicada' WHERE id = :id AND tenant_id = :t"
            ),
            {"id": solicitud_id, "t": tenant},
        )
        await registrar_evento(
            db,
            tenant,
            evento="mejora_plan_aplicada",
            resumen=f"Mejora de plan aplicada: {codigo}",
            actor_tipo="sistema",
            actor_id=svc.actor,
            datos={"solicitud_id": solicitud_id, "plan_deseado": codigo},
        )
        solicitud["estado"] = "aplicada"
    return SolicitudMejora.model_validate(solicitud)
