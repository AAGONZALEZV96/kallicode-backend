"""Organización (tenant): GET y PATCH (§7.6–7.7 del diseño)."""
from __future__ import annotations

from zoneinfo import available_timezones

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import plan_vigente
from kallicode_core.config import get_settings
from kallicode_core.db import uno
from kallicode_core.errors import AppError
from kallicode_core.logging import log

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual

router = APIRouter(prefix="/organization", tags=["Organización"])


class OrgPatch(BaseModel):
    """Campos actualizables del tenant (parcial)."""
    nombre: str | None = Field(default=None, min_length=2, max_length=160)
    idioma: str | None = Field(default=None, pattern=r"^(es|en|pt)$")
    timezone: str | None = None


async def _org(db, tenant_id: str) -> dict:
    s = get_settings()
    org = await uno(db, "SELECT * FROM core.organizations WHERE id = :t", {"t": tenant_id})
    plan = await plan_vigente(db, tenant_id)
    return {"id": org["id"], "nombre": org["nombre"], "idioma": org["idioma"],
            "timezone": org["timezone"], "despliegue": org["despliegue"],
            "fabrica_activa": org["fabrica_activa"],
            "plan": plan and {"codigo": plan["codigo"], "nombre": plan["nombre"],
                              "lineas": plan["lineas"], "loc_mes": plan["loc_mes"],
                              "renueva_el": str(plan["renueva_el"])},
            "polling": {"notificaciones_s": s.poll_notificaciones_s,
                        "tablero_s": s.poll_tablero_s}}


@router.get("", summary="Datos de la organización")
async def obtener(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: datos generales + plan vigente + intervalos de polling (D12)."""
    async with sesion_de(usuario) as db:
        return await _org(db, usuario.tenant_id)


@router.patch("", summary="Actualizar organización")
async def actualizar(datos: OrgPatch,
                     usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Errores: TIMEZONE_INVALIDA (422). Log+auditoría: organizacion_modificada."""
    if datos.timezone and datos.timezone not in available_timezones():
        raise AppError("TIMEZONE_INVALIDA", 422, "La zona horaria indicada no existe.")
    cambios = datos.model_dump(exclude_none=True)
    async with sesion_de(usuario) as db:
        for campo, v in cambios.items():
            await db.execute(text(
                f"UPDATE core.organizations SET {campo} = :v WHERE id = :t"),
                {"v": v, "t": usuario.tenant_id})
        if cambios:
            await registrar_evento(db, usuario.tenant_id, evento="organizacion_modificada",
                                   resumen="Organización actualizada", actor_tipo="humano",
                                   actor_id=usuario.user_id, datos={"campos": list(cambios)})
        salida = await _org(db, usuario.tenant_id)
    log.info("organization.actualizada", campos=list(cambios))
    return salida
