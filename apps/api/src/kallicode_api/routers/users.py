"""Usuarios y perfil (§7 del diseño): me, listado, invitaciones, rol/estado."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado, permiso_denegado
from kallicode_core.ids import nuevo
from kallicode_core.logging import hash_email, log
from kallicode_core.seguridad import (ROLES, generar_token_opaco, hashear_password,
                                      validar_politica_password, verificar_password)

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual

router = APIRouter(prefix="/users", tags=["Usuarios"])


class MePatch(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=120)
    idioma: str | None = Field(default=None, pattern=r"^(es|en|pt)$")
    notificaciones: dict | None = None
    password_actual: str | None = None
    password_nueva: str | None = None


class InviteIn(BaseModel):
    email: EmailStr
    rol: str = Field(pattern=r"^(owner|admin|architect|approver|member|viewer)$")
    mensaje: str | None = Field(default=None, max_length=500)


class UserPatch(BaseModel):
    rol: str | None = Field(default=None, pattern=r"^(owner|admin|architect|approver|member|viewer)$")
    estado: str | None = Field(default=None, pattern=r"^(activo|desactivado)$")


def _perfil(u: dict) -> dict:
    puede = [1, 2] if u["rol"] in ("owner", "admin", "approver") else []
    if u["rol"] == "architect":
        puede = [1, 2, 3]
    return {"id": u["id"], "nombre": u["nombre"], "email": u["email"], "rol": u["rol"],
            "idioma": u["idioma"], "estado": u["estado"],
            "notificaciones": u["notificaciones"], "puede_firmar": puede,
            "ultimo_acceso": u["ultimo_acceso"]}


@router.get("/me", summary="Perfil propio")
async def me(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    async with sesion_de(usuario) as db:
        u = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": usuario.user_id})
    return _perfil(u)


@router.patch("/me", summary="Actualizar perfil propio")
async def patch_me(datos: MePatch,
                   usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Errores: PASSWORD_ACTUAL_INCORRECTA (400) · AUTH_PASSWORD_DEBIL (422).
    El cambio de contraseña revoca el resto de sesiones."""
    async with sesion_de(usuario) as db:
        u = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": usuario.user_id})
        if datos.password_nueva:
            if not datos.password_actual or \
                    not verificar_password(datos.password_actual, u["password_hash"]):
                raise AppError("PASSWORD_ACTUAL_INCORRECTA", 400,
                               "La contraseña actual no es correcta.")
            validar_politica_password(datos.password_nueva)
            await db.execute(text("UPDATE core.users SET password_hash=:p WHERE id=:i"),
                             {"p": hashear_password(datos.password_nueva), "i": u["id"]})
            await db.execute(text("UPDATE core.refresh_tokens SET revocado=true "
                                  "WHERE user_id=:i"), {"i": u["id"]})
            await registrar_evento(db, usuario.tenant_id, evento="usuario_modificado",
                                   resumen="Contraseña cambiada", actor_tipo="humano",
                                   actor_id=u["id"])
        import json
        for campo in ("nombre", "idioma"):
            v = getattr(datos, campo)
            if v is not None:
                await db.execute(text(f"UPDATE core.users SET {campo}=:v WHERE id=:i"),
                                 {"v": v, "i": u["id"]})
        if datos.notificaciones is not None:
            await db.execute(text("UPDATE core.users SET notificaciones = "
                                  "notificaciones || CAST(:n AS jsonb) WHERE id=:i"),
                             {"n": json.dumps(datos.notificaciones), "i": u["id"]})
        u = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": u["id"]})
    log.info("users.me.actualizado", campos=list(datos.model_dump(exclude_none=True)))
    return _perfil(u)


@router.get("", summary="Listar usuarios de la organización")
async def listar(rol: str | None = None, estado: str | None = None,
                 q: str | None = Query(default=None, max_length=100),
                 page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=25, ge=1, le=100),
                 usuario: UsuarioActual = Depends(
                     requiere_rol("owner", "admin", "architect", "approver"))) -> dict:
    """Filtros: rol, estado, q (nombre/email). Incluye invitados pendientes."""
    cond, params = ["tenant_id = :t"], {"t": usuario.tenant_id}
    if rol:
        cond.append("rol = :rol"); params["rol"] = rol
    if estado:
        cond.append("estado = :estado"); params["estado"] = estado
    if q:
        cond.append("(nombre ILIKE :q OR email ILIKE :q)"); params["q"] = f"%{q}%"
    where = " AND ".join(cond)
    async with sesion_de(usuario) as db:
        items = await todos(db, f"""SELECT id, nombre, email, rol, estado, ultimo_acceso
                                     FROM core.users WHERE {where}
                                     ORDER BY nombre LIMIT :lim OFFSET :off""",
                            {**params, "lim": page_size, "off": (page - 1) * page_size})
        total = (await uno(db, f"SELECT count(*) AS n FROM core.users WHERE {where}",
                           params))["n"]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/invitations", status_code=201, summary="Invitar usuario")
async def invitar(datos: InviteIn,
                  usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Errores: INVITACION_DUPLICADA (409) · ROL_NO_AUTORIZADO (403) ·
    LIMITE_USUARIOS_PLAN (402). Comercial: cupo de usuarios del plan."""
    if datos.rol in ("owner", "admin") and usuario.rol != "owner":
        raise AppError("ROL_NO_AUTORIZADO", 403,
                       "Solo el propietario puede invitar administradores.")
    email = datos.email.lower()
    async with sesion_de(usuario) as db:
        await comercial.verificar_cupo_usuarios(db, usuario.tenant_id)
        dup = await uno(db, """SELECT id FROM core.invitations
                                WHERE tenant_id=:t AND email=:e AND estado='enviada'""",
                        {"t": usuario.tenant_id, "e": email})
        if dup:
            raise AppError("INVITACION_DUPLICADA", 409,
                           "Ya hay una invitación pendiente para este correo.")
        claro, h = generar_token_opaco("kcin")
        iid = nuevo("inv")
        fila = await uno(db, """
            INSERT INTO core.invitations (id, tenant_id, email, rol, token_hash,
                                          invitado_por, expira_en)
            VALUES (:i, :t, :e, :r, :h, :u, now() + interval '7 days')
            RETURNING id, email, rol, estado, expira_en""",
            {"i": iid, "t": usuario.tenant_id, "e": email, "r": datos.rol,
             "h": h, "u": usuario.user_id})
        await registrar_evento(db, usuario.tenant_id, evento="usuario_invitado",
                               resumen=f"Invitación enviada ({datos.rol})",
                               actor_tipo="humano", actor_id=usuario.user_id)
        await comercial.redis_cliente().xadd("kc:correo", {
            "tipo": "invitacion", "email": email, "token": claro,
            "mensaje": datos.mensaje or "", "tenant_id": usuario.tenant_id})
    log.info("users.invitacion.creada", email=hash_email(email), rol=datos.rol)
    return fila


@router.patch("/{user_id}", summary="Actualizar usuario (rol / estado)")
async def patch_user(user_id: str, datos: UserPatch,
                     usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Errores: ULTIMO_OWNER / ULTIMO_ARQUITECTO / AUTODESACTIVACION (409/400).
    Desactivar revoca las sesiones; nunca hay borrado físico (compliance)."""
    if datos.estado == "desactivado" and user_id == usuario.user_id:
        raise AppError("AUTODESACTIVACION", 400, "No puedes desactivar tu propia cuenta.")
    async with sesion_de(usuario) as db:
        u = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": user_id})
        if not u:
            raise no_encontrado("El usuario")
        if (u["rol"] in ("owner", "admin") or datos.rol in ("owner",)) and usuario.rol != "owner":
            raise permiso_denegado("Solo el propietario puede gestionar este usuario.")
        if datos.rol and datos.rol != "owner" and u["rol"] == "owner":
            owners = (await uno(db, """SELECT count(*) AS n FROM core.users
                                        WHERE tenant_id=:t AND rol='owner' AND estado='activo'""",
                                {"t": usuario.tenant_id}))["n"]
            if owners <= 1:
                raise AppError("ULTIMO_OWNER", 409,
                               "La organización debe tener al menos un propietario.")
        if (datos.rol and u["rol"] == "architect" and datos.rol != "architect") or \
                (datos.estado == "desactivado" and u["rol"] == "architect"):
            arqs = (await uno(db, """SELECT count(*) AS n FROM core.users
                                      WHERE tenant_id=:t AND rol='architect'
                                        AND estado='activo' AND id <> :i""",
                              {"t": usuario.tenant_id, "i": user_id}))["n"]
            if arqs == 0:
                raise AppError("ULTIMO_ARQUITECTO", 409,
                               "No puedes quitar el rol: es el único usuario que puede "
                               "firmar el Gate 3.")
        if datos.estado == "activo" and u["estado"] == "desactivado":
            await comercial.verificar_cupo_usuarios(db, usuario.tenant_id)
        cambios = datos.model_dump(exclude_none=True)
        for campo, v in cambios.items():
            await db.execute(text(f"UPDATE core.users SET {campo}=:v WHERE id=:i"),
                             {"v": v, "i": user_id})
        if datos.estado == "desactivado":
            await db.execute(text("UPDATE core.refresh_tokens SET revocado=true "
                                  "WHERE user_id=:i"), {"i": user_id})
        await registrar_evento(db, usuario.tenant_id, evento="usuario_modificado",
                               resumen=f"Usuario modificado: {', '.join(cambios)}",
                               actor_tipo="humano", actor_id=usuario.user_id,
                               datos={"user_id": user_id, **cambios})
        u = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": user_id})
    log.info("users.actualizado", user_id=user_id, cambios=list(cambios))
    return _perfil(u)
