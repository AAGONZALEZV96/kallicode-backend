"""Dependencias de autenticación, autorización y aislamiento por tenant."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request
from kallicode_core import comercial
from kallicode_core.db import sesion_tenant
from kallicode_core.errors import (
    AppError,
    no_autenticado,
    permiso_denegado,
)
from kallicode_core.logging import bind_contexto
from kallicode_core.seguridad import (
    ClaimsServicio,
    ClaimsUsuario,
    estado_revocacion,
    validar_claims_servicio,
    validar_claims_usuario,
    verificar_token,
)

SERVICIOS_PERMITIDOS: frozenset[str] = frozenset(
    {
        "orquestador",
        "normalizador",
        "motor-pruebas",
        "ingesta",
    }
)


@dataclass
class UsuarioActual:
    user_id: str
    tenant_id: str
    rol: str
    restringido: bool

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"


@dataclass
class ServicioActual:
    servicio: str
    tenant_id: str
    linea: int | None

    @property
    def actor(self) -> str:
        return f"svc:{self.servicio}"


def _extraer_bearer(authorization: str | None) -> str:
    if (
        not authorization
        or not authorization.lower().startswith("bearer ")
    ):
        raise no_autenticado()

    return authorization.split(" ", 1)[1]


async def comprobar_revocacion(
    claims: ClaimsUsuario | ClaimsServicio,
) -> str | None:
    """Devuelve el rol vigente o rechaza un token revocado."""
    revocado, sello, rol = await estado_revocacion(
        claims.org,
        claims.sub,
        claims.jti,
    )

    if revocado or (
        sello is not None
        and claims.iat <= sello
    ):
        raise AppError(
            "AUTH_TOKEN_REVOCADO",
            401,
            "La sesión fue cerrada o revocada.",
        )

    return rol


async def usuario_actual(
    request: Request,
    authorization: str | None = Header(default=None),
) -> UsuarioActual:
    claims = validar_claims_usuario(
        verificar_token(
            _extraer_bearer(authorization),
            "user",
        )
    )
    rol_vigente = await comprobar_revocacion(claims)

    usuario = UsuarioActual(
        user_id=claims.sub,
        tenant_id=claims.org,
        rol=rol_vigente or claims.rol,
        restringido=claims.restringido,
    )

    bind_contexto(
        tenant_id=usuario.tenant_id,
        actor=usuario.actor,
    )

    await comercial.rl_usuario(
        usuario.user_id,
        usuario.tenant_id,
    )

    if (
        usuario.restringido
        and not request.url.path.startswith(
            (
                "/api/v1/usage",
                "/api/v1/plans",
                "/api/v1/organization",
                "/api/v1/auth",
            )
        )
    ):
        raise permiso_denegado(
            "Suscripción suspendida: acceso limitado a Consumo y plan."
        )

    request.state.usuario = usuario
    return usuario


def requiere_rol(*roles: str):
    """Exige autenticación de usuario y uno de los roles indicados."""

    async def _dep(
        usuario: UsuarioActual = Depends(usuario_actual),
    ) -> UsuarioActual:
        if usuario.rol not in roles:
            raise permiso_denegado()

        return usuario

    return _dep


async def servicio_actual(
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> ServicioActual:
    """Exige JWT interno, identidad declarada e Idempotency-Key."""
    claims = validar_claims_servicio(
        verificar_token(
            _extraer_bearer(authorization),
            "svc",
        )
    )

    if claims.svc not in SERVICIOS_PERMITIDOS:
        raise permiso_denegado(
            f"Identidad de servicio '{claims.svc}' no autorizada."
        )

    if claims.sub != f"svc:{claims.svc}":
        raise no_autenticado(
            "La identidad del token de servicio es inconsistente."
        )

    if not idempotency_key or not idempotency_key.strip():
        raise no_autenticado(
            "La API interna exige cabecera Idempotency-Key."
        )

    servicio = ServicioActual(
        servicio=claims.svc,
        tenant_id=claims.org,
        linea=claims.line,
    )

    bind_contexto(
        tenant_id=servicio.tenant_id,
        actor=servicio.actor,
    )

    return servicio


def sesion_de(
    identidad: UsuarioActual | ServicioActual,
):
    """Abre una sesión RLS para el tenant y actor autenticados."""
    return sesion_tenant(
        identidad.tenant_id,
        actor=identidad.actor,
    )