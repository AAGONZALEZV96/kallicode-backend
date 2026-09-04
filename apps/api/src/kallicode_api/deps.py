"""Dependencias FastAPI compartidas: autenticación, tenant/RLS y autorización.

Patrones:
    usuario: UsuarioActual = Depends(usuario_actual)          -> JWT de usuario
    usuario = Depends(requiere_rol("admin", "owner"))         -> además exige rol
    svc: ServicioActual = Depends(servicio_actual)            -> JWT de servicio (interna)
    async with sesion_de(usuario) as db: ...                  -> sesión RLS del tenant
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request

from kallicode_core import comercial
from kallicode_core.db import sesion_tenant
from kallicode_core.errors import no_autenticado, permiso_denegado
from kallicode_core.seguridad import verificar_token


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
    if not authorization or not authorization.lower().startswith("bearer "):
        raise no_autenticado()
    return authorization.split(" ", 1)[1]


async def usuario_actual(request: Request,
                         authorization: str | None = Header(default=None)) -> UsuarioActual:
    """Valida el JWT de usuario, aplica RL-1 y expone la identidad."""
    claims = verificar_token(_extraer_bearer(authorization), "user")
    u = UsuarioActual(user_id=claims["sub"], tenant_id=claims["org"],
                      rol=claims["rol"], restringido=claims.get("restringido", False))
    await comercial.rl_usuario(u.user_id, u.tenant_id)
    if u.restringido and not request.url.path.startswith(("/api/v1/usage", "/api/v1/plans",
                                                          "/api/v1/organization",
                                                          "/api/v1/auth")):
        raise permiso_denegado("Suscripción suspendida: acceso limitado a Consumo y plan.")
    request.state.usuario = u
    return u


def requiere_rol(*roles: str):
    """Dependencia que además del JWT exige uno de los roles indicados."""
    async def _dep(usuario: UsuarioActual = Depends(usuario_actual)) -> UsuarioActual:
        if usuario.rol not in roles:
            raise permiso_denegado()
        return usuario
    return _dep


async def servicio_actual(authorization: str | None = Header(default=None),
                          idempotency_key: str | None = Header(default=None,
                                                               alias="Idempotency-Key")
                          ) -> ServicioActual:
    """Valida el JWT de servicio de la API interna (typ=svc).

    La API interna exige además cabecera Idempotency-Key (§17): los workers
    reintentan y el estado debe converger.
    """
    claims = verificar_token(_extraer_bearer(authorization), "svc")
    if not idempotency_key:
        raise no_autenticado("La API interna exige cabecera Idempotency-Key.")
    return ServicioActual(servicio=claims["svc"], tenant_id=claims["org"],
                          linea=claims.get("line"))


def sesion_de(identidad: UsuarioActual | ServicioActual):
    """Sesión transaccional RLS para la identidad (usuario o servicio)."""
    return sesion_tenant(identidad.tenant_id, actor=identidad.actor)
