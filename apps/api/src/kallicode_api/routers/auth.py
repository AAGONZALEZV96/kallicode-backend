"""Autenticación y sesión (§6 del diseño de endpoints).

Endpoints: login, refresh, logout, forgot-password, reset-password,
invitations/accept. JWT propio RS256; refresh rotativo de un solo uso.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from kallicode_api.servicios import boveda
from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.errors import AppError, no_autenticado
from kallicode_core.ids import nuevo
from kallicode_core.logging import hash_email, log
from kallicode_core.seguridad import (
    emitir_access_token,
    generar_token_opaco,
    hash_token,
    hashear_password,
    invalidar_usuario,
    requiere_rehash,
    revocar_jti,
    validar_politica_password,
    verificar_password,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ---------------------------------------------------------------- esquemas
class LoginIn(BaseModel):
    """Entrada de login. email se normaliza a minúsculas."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    org_id: str | None = None


class TokensOut(BaseModel):
    """Salida de login/refresh/accept: par de tokens + perfil resumido."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    usuario: dict
    acceso_restringido: bool = False


class RefreshIn(BaseModel):
    refresh_token: str = Field(pattern=r"^kcrt_")


class LogoutIn(BaseModel):
    refresh_token: str
    todas: bool = False


class ForgotIn(BaseModel):
    email: EmailStr
    org_id: str | None = None


class ResetIn(BaseModel):
    token: str = Field(pattern=r"^kcpr_")
    password_nueva: str = Field(min_length=12, max_length=128)


class AcceptIn(BaseModel):
    token: str = Field(pattern=r"^kcin_")
    nombre: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    idioma: str | None = Field(default=None, pattern=r"^(es|en|pt)$")


# ---------------------------------------------------------------- funciones auxiliares
def obtener_ip_real(request: Request) -> str:
    """Extrae la IP real del cliente, considerando proxies y balanceadores."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # El primer valor es la IP original del cliente
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    return request.client.host if request.client else "?"


async def _emitir_par(db, user: dict, ip: str | None, ua: str | None) -> TokensOut:
    """Emite access+refresh, persiste el refresh (hash) y arma la respuesta."""
    s = get_settings()
    sub = await uno(
        db,
        """SELECT s.estado FROM core.subscriptions s
                            WHERE s.tenant_id = :t AND s.finaliza_el IS NULL""",
        {"t": user["tenant_id"]},
    )
    restringido = bool(sub and sub["estado"] == "suspendida")
    claro, h = generar_token_opaco("kcrt")
    await db.execute(
        text("""
        INSERT INTO core.refresh_tokens (id, tenant_id, user_id, token_hash,
                                         expira_en, ip, user_agent)
        VALUES (:id, :t, :u, :h, :exp, :ip, :ua)"""),
        {
            "id": nuevo("rt"),
            "t": user["tenant_id"],
            "u": user["id"],
            "h": h,
            "exp": datetime.now(UTC) + timedelta(days=s.refresh_token_ttl_dias),
            "ip": ip,
            "ua": (ua or "")[:400],
        },
    )
    return TokensOut(
        access_token=emitir_access_token(user["id"], user["tenant_id"], user["rol"], restringido),
        refresh_token=claro,
        usuario={
            "id": user["id"],
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": user["rol"],
            "idioma": user["idioma"],
            "org_id": user["tenant_id"],
        },
        acceso_restringido=restringido,
    )


# ---------------------------------------------------------------- endpoints
@router.post("/login", response_model=TokensOut, summary="Iniciar sesión")
async def login(datos: LoginIn, request: Request) -> TokensOut:
    """Valida credenciales y emite tokens."""
    ip = obtener_ip_real(request)
    await comercial.rl_login(ip)
    email = datos.email.lower()

    # Bloqueo por intentos (5 fallos -> 15 min) en Redis (par email + IP)
    r = comercial.redis_cliente()
    hash_correo = hash_email(email)
    clave_fallos = f"login:fallos:{hash_correo}:{ip}"
    clave_bloqueo = f"login:bloqueo:{hash_correo}:{ip}"

    if await r.exists(clave_bloqueo):
        raise AppError(
            "AUTH_CUENTA_BLOQUEADA",
            423,
            "Demasiados intentos fallidos desde esta IP. Inténtalo de nuevo en 15 minutos.",
        )

    async with sesion_sistema() as db:
        filas = await todos(
            db,
            """SELECT u.*, o.nombre AS org_nombre 
               FROM core.users u
               JOIN core.organizations o ON o.id = u.tenant_id
               WHERE u.email = :e AND o.eliminado_en IS NULL
               LIMIT 20""",
            {"e": email},
        )

        user = None
        if filas:
            if len(filas) > 1 and not datos.org_id:
                raise AppError(
                    "AUTH_MULTIPLE_TENANTS",
                    409,
                    "El correo pertenece a múltiples organizaciones. Selecciona una para continuar.",
                )

            if datos.org_id:
                user_match = next((f for f in filas if f["tenant_id"] == datos.org_id), None)
                if user_match:
                    user = dict(user_match)
            else:
                user = dict(filas[0])

    if (
        not user
        or not user["password_hash"]
        or not verificar_password(datos.password, user["password_hash"])
    ):
        n = await r.incr(clave_fallos)
        await r.expire(clave_fallos, 900)
        if n >= 5:
            await r.setex(clave_bloqueo, 900, "1")
            log.warning("auth.login.bloqueo", email=hash_correo, ip=ip)
        log.warning("auth.login.fallo", email=hash_correo, ip=ip, intento=n)
        raise no_autenticado("Correo o contraseña incorrectos.")
    else:
        # TICKET BE-F33: Si las credenciales son correctas, verificamos si el hash
        # se quedó atrás respecto a los parámetros de seguridad actuales.
        if requiere_rehash(user["password_hash"]):
            nuevo_hash = hashear_password(datos.password)
            async with sesion_sistema() as db_sis:
                await db_sis.execute(
                    text("UPDATE core.users SET password_hash = :p WHERE id = :u"),
                    {"p": nuevo_hash, "u": user["id"]},
                )
            log.info("auth.login.rehash_aplicado", user_id=user["id"])

    if user["estado"] != "activo":
        raise AppError(
            "AUTH_CUENTA_DESACTIVADA",
            403,
            "Tu cuenta está desactivada. Contacta al administrador.",
        )

    await r.delete(clave_fallos)
    async with sesion_tenant(user["tenant_id"], actor=f"user:{user['id']}") as db:
        salida = await _emitir_par(db, user, ip, request.headers.get("user-agent"))
        await db.execute(
            text("UPDATE core.users SET ultimo_acceso = now() WHERE id = :u"),
            {"u": user["id"]},
        )
        await registrar_evento(
            db,
            user["tenant_id"],
            evento="sesion_iniciada",
            resumen="Inicio de sesión",
            actor_tipo="humano",
            actor_id=user["id"],
        )
    log.info("auth.login.ok", user_id=user["id"], tenant_id=user["tenant_id"], ip=ip)
    return salida


@router.post("/refresh", response_model=TokensOut, summary="Renovar tokens")
async def refresh(datos: RefreshIn, request: Request) -> TokensOut:
    """Rotación de refresh: un solo uso; un token reutilizado revoca todas
    las sesiones del usuario (detección de replay).

    Errores: AUTH_REFRESH_INVALIDO (401) · AUTH_REFRESH_REUTILIZADO (401).
    """
    h = hash_token(datos.refresh_token)
    async with sesion_sistema() as db:
        fila = await uno(db, "SELECT * FROM core.refresh_tokens WHERE token_hash = :h", {"h": h})
        if (
            not fila
            or fila["revocado"]
            or fila["expira_en"].replace(tzinfo=UTC) < datetime.now(UTC)
        ):
            raise no_autenticado("La sesión ha expirado. Inicia sesión de nuevo.")
        if fila["usado_en"] is not None:
            # replay: revocación masiva + alerta
            await db.execute(
                text("UPDATE core.refresh_tokens SET revocado = true WHERE user_id = :u"),
                {"u": fila["user_id"]},
            )
            log.error("auth.refresh.replay", user_id=fila["user_id"])
            raise AppError(
                "AUTH_REFRESH_REUTILIZADO",
                401,
                "Por seguridad hemos cerrado todas tus sesiones. Inicia sesión de nuevo.",
            )
        await db.execute(
            text("UPDATE core.refresh_tokens SET usado_en = now() WHERE id = :i"),
            {"i": fila["id"]},
        )
        user = await uno(
            db,
            "SELECT * FROM core.users WHERE id = :u AND estado='activo'",
            {"u": fila["user_id"]},
        )
        if not user:
            raise no_autenticado()

    ip = obtener_ip_real(request)
    async with sesion_tenant(user["tenant_id"], actor=f"user:{user['id']}") as db:
        salida = await _emitir_par(
            db,
            user,
            ip,
            request.headers.get("user-agent"),
        )
    log.info("auth.refresh.ok", user_id=user["id"])
    return salida


@router.post("/logout", summary="Cerrar sesión")
async def logout(datos: LogoutIn, request: Request) -> dict:
    """Revoca el refresh de la sesión (o todas con todas=true).

    Salida: {"cerradas": n}. Error: AUTH_TOKEN_AJENO (403).
    """
    from kallicode_core.seguridad import verificar_token

    auth = request.headers.get("authorization", "")
    claims = verificar_token(auth.split(" ", 1)[1] if " " in auth else "", "user")
    h = hash_token(datos.refresh_token)
    async with sesion_tenant(claims["org"], actor=f"user:{claims['sub']}") as db:
        fila = await uno(
            db, "SELECT user_id FROM core.refresh_tokens WHERE token_hash=:h", {"h": h}
        )
        if fila and fila["user_id"] != claims["sub"]:
            raise AppError("AUTH_TOKEN_AJENO", 403, "No puedes cerrar sesiones de otro usuario.")
        if datos.todas:
            n = len(
                await todos(
                    db,
                    """UPDATE core.refresh_tokens SET revocado = true
                                        WHERE user_id = :u AND NOT revocado RETURNING id""",
                    {"u": claims["sub"]},
                )
            )
        else:
            n = len(
                await todos(
                    db,
                    """UPDATE core.refresh_tokens SET revocado = true
                                        WHERE token_hash = :h AND NOT revocado RETURNING id""",
                    {"h": h},
                )
            )
        await registrar_evento(
            db,
            claims["org"],
            evento="sesion_cerrada",
            resumen="Sesión cerrada",
            actor_tipo="humano",
            actor_id=claims["sub"],
        )

    ttl = int(claims["exp"]) - int(time.time())
    await revocar_jti(claims["org"], claims["jti"], ttl)
    if datos.todas:
        await invalidar_usuario(claims["org"], claims["sub"])
    log.info("auth.logout", user_id=claims["sub"], cerradas=n)
    return {"cerradas": n}


@router.post("/forgot-password", status_code=202, summary="Solicitar restablecimiento")
async def forgot_password(datos: ForgotIn) -> dict:
    """Respuesta SIEMPRE 202 idéntica, exista o no la cuenta (no revela nada).

    El worker notifier envía el correo con el token kcpr_ (30 min, un uso).
    Throttling: 3 solicitudes / 15 min por email (silencioso).
    """
    email = datos.email.lower()
    r = comercial.redis_cliente()
    clave = f"forgot:{hash_email(email)}"
    n = await r.incr(clave)
    await r.expire(clave, 900)
    if n <= 3:
        async with sesion_sistema() as db:
            q = "SELECT id, tenant_id FROM core.users WHERE email=:e AND estado='activo' LIMIT 20"
            params = {"e": email}

            if datos.org_id:
                q = "SELECT id, tenant_id FROM core.users WHERE email=:e AND tenant_id=:t AND estado='activo' LIMIT 1"
                params["t"] = datos.org_id

            users = await todos(db, q, params)

            for user in users:
                claro, h = generar_token_opaco("kcpr")
                # El Hash sigue yendo a la BD para verificación
                await db.execute(
                    text("""
                    INSERT INTO core.password_reset_tokens
                        (id, tenant_id, user_id, token_hash, expira_en)
                    VALUES (:i, :t, :u, :h, now() + interval '30 minutes')"""),
                    {"i": nuevo("pr"), "t": user["tenant_id"], "u": user["id"], "h": h},
                )

                # --- INICIO MODIFICACIÓN BE-F05 ---
                # 1. Guardamos el token en plano dentro de la bóveda
                ref_nombre = f"reset-{user['id']}"
                await boveda.guardar(user["tenant_id"], ref_nombre, {"token": claro})

                # 2. Enviamos solo la referencia por el stream, no el token
                await r.xadd(
                    "kc:correo",
                    {
                        "tipo": "reset_password",
                        "user_id": user["id"],
                        "token_ref": ref_nombre,
                        "tenant_id": user["tenant_id"],
                    },
                    maxlen=10000,
                    approximate=True,
                )
                # --- FIN MODIFICACIÓN ---

        log.info("auth.forgot.solicitado", email=hash_email(email))
    else:
        log.warning("auth.forgot.exceso", email=hash_email(email))
    return {"mensaje": "Si la cuenta existe, enviamos un correo con instrucciones."}


@router.post("/reset-password", summary="Restablecer contraseña")
async def reset_password(datos: ResetIn) -> dict:
    """Consume el token kcpr_, valida la política y revoca todas las sesiones.

    Errores: AUTH_TOKEN_RESET_INVALIDO (400) · AUTH_PASSWORD_DEBIL (422).
    """
    validar_politica_password(datos.password_nueva)
    h = hash_token(datos.token)
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """SELECT * FROM core.password_reset_tokens
                                 WHERE token_hash = :h AND usado_en IS NULL
                                   AND expira_en > now()""",
            {"h": h},
        )
        if not fila:
            raise AppError(
                "AUTH_TOKEN_RESET_INVALIDO",
                400,
                "El enlace ha expirado o ya fue usado. Solicita uno nuevo.",
            )
        await db.execute(
            text("""
            UPDATE core.password_reset_tokens SET usado_en = now() WHERE id = :i"""),
            {"i": fila["id"]},
        )
        await db.execute(
            text("""
            UPDATE core.users SET password_hash = :p WHERE id = :u"""),
            {"p": hashear_password(datos.password_nueva), "u": fila["user_id"]},
        )
        await db.execute(
            text("""
            UPDATE core.refresh_tokens SET revocado = true WHERE user_id = :u"""),
            {"u": fila["user_id"]},
        )
        await registrar_evento(
            db,
            fila["tenant_id"],
            evento="password_restablecida",
            resumen="Contraseña restablecida",
            actor_tipo="humano",
            actor_id=fila["user_id"],
        )
        await invalidar_usuario(fila["tenant_id"], fila["user_id"])

    log.info("auth.reset.ok", user_id=fila["user_id"])
    return {"mensaje": "Contraseña actualizada. Inicia sesión con tu nueva contraseña."}


@router.post(
    "/invitations/accept",
    response_model=TokensOut,
    summary="Aceptar invitación (alta de usuario)",
)
async def aceptar_invitacion(datos: AcceptIn, request: Request) -> TokensOut:
    """Completa el alta del invitado: nombre + contraseña, activa y emite tokens.

    Errores: INVITACION_INVALIDA (400) · USUARIO_YA_EXISTE (409) ·
             LIMITE_USUARIOS_PLAN (402) · AUTH_PASSWORD_DEBIL (422).
    Comercial: re-verifica el cupo de usuarios AL ACEPTAR, no solo al invitar.
    """
    validar_politica_password(datos.password)
    h = hash_token(datos.token)
    async with sesion_sistema() as db:
        inv = await uno(
            db,
            """SELECT * FROM core.invitations
                                WHERE token_hash = :h AND estado = 'enviada'
                                  AND expira_en > now()""",
            {"h": h},
        )
    if not inv:
        raise AppError(
            "INVITACION_INVALIDA",
            400,
            "La invitación ha expirado o ya fue utilizada. Pide al administrador que la reenvíe.",
        )
    async with sesion_tenant(inv["tenant_id"], actor="sistema") as db:
        existe = await uno(
            db,
            "SELECT id FROM core.users WHERE email = :e AND estado='activo'",
            {"e": inv["email"]},
        )
        if existe:
            raise AppError(
                "USUARIO_YA_EXISTE",
                409,
                "Ya existe una cuenta con este correo. Inicia sesión.",
            )
        await comercial.verificar_cupo_usuarios(db, inv["tenant_id"])
        org = await uno(
            db,
            "SELECT idioma FROM core.organizations WHERE id = :t",
            {"t": inv["tenant_id"]},
        )
        uid = nuevo("u")
        await db.execute(
            text("""
            INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado,
                                    idioma, password_hash)
            VALUES (:i, :t, :e, :n, :r, 'activo', :idi, :p)"""),
            {
                "i": uid,
                "t": inv["tenant_id"],
                "e": inv["email"],
                "n": datos.nombre,
                "r": inv["rol"],
                "idi": datos.idioma or (org["idioma"] if org else "es"),
                "p": hashear_password(datos.password),
            },
        )
        await db.execute(
            text(
                """
            UPDATE core.invitations SET estado='aceptada', usado_en=now() WHERE id=:i"""
            ),
            {"i": inv["id"]},
        )
        await registrar_evento(
            db,
            inv["tenant_id"],
            evento="usuario_creado",
            resumen=f"Usuario creado por invitación ({inv['rol']})",
            actor_tipo="humano",
            actor_id=uid,
            datos={"invitado_por": inv["invitado_por"]},
        )
        user = await uno(db, "SELECT * FROM core.users WHERE id = :i", {"i": uid})

        ip = obtener_ip_real(request)
        salida = await _emitir_par(
            db,
            user,
            ip,
            request.headers.get("user-agent"),
        )
    log.info("auth.invitacion.aceptada", user_id=uid, invitado_por=inv["invitado_por"])
    return salida
