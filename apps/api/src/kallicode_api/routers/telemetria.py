"""Telemetría del portal (KC-PT-11-5): errores de cliente agrupados y Web Vitals.

El portal junta los errores que no se manejaron (por firma: tipo, mensaje y origen) y las
Web Vitals de la visita, y los manda en lotes. Aquí:

- se aceptan con o sin sesión: el login y las pantallas públicas también fallan. Con un token
  válido el registro lleva la organización y la persona; sin él, es anónimo;
- cada lote tiene topes (20 errores, 10 métricas, textos cortos) y un límite de frecuencia por
  persona o por IP (RL de telemetría), para que un cliente roto no inunde los registros;
- cada grupo de errores queda en el registro estructurado (`portal.error_cliente`) con el
  trace_id del backend, si el error vino de la API, y los contadores salen en /metrics
  (kc_portal_errores, kc_portal_web_vitals);
- los textos se recortan y se les quitan correos y tokens antes de registrarlos.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Header, Request
from kallicode_core.limites import rl_telemetria
from kallicode_core.logging import log
from kallicode_core.redis_keys import clave_tenant
from kallicode_core.seguridad import validar_claims_usuario, verificar_token
from kallicode_core.telemetria import registrar_portal
from pydantic import BaseModel, ConfigDict, Field

from .auth import obtener_ip_real

router = APIRouter(prefix="/telemetry", tags=["Telemetría"])

TipoError = Literal["error", "rechazo", "limite", "contrato"]
NombreVital = Literal["LCP", "INP", "CLS", "FCP", "TTFB"]
Calificacion = Literal["good", "needs-improvement", "poor"]


class ErrorClienteIn(BaseModel):
    """Un grupo de errores iguales en el navegador."""

    model_config = ConfigDict(extra="forbid")

    firma: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    tipo: TipoError = Field(
        description="error: excepción no capturada; rechazo: promesa rechazada sin manejar; "
        "limite: lo atrapó un límite de error de React; contrato: una respuesta de la API no "
        "cumplió el contrato (api-v1 y los tipos del portal se separaron)."
    )
    mensaje: str = Field(max_length=300)
    ruta: str = Field(max_length=200, description="Ruta sin identificadores: /tickets/:numero.")
    veces: int = Field(ge=1, le=1000)
    trace_id: str | None = Field(default=None, max_length=64)


class VitalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: NombreVital
    valor: float = Field(ge=0, le=600_000, description="Milisegundos, salvo CLS (sin unidad).")
    calificacion: Calificacion
    ruta: str = Field(max_length=200)


class TelemetriaClienteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errores: list[ErrorClienteIn] = Field(default_factory=list, max_length=20)
    vitals: list[VitalIn] = Field(default_factory=list, max_length=10)


class TelemetriaAceptadaOut(BaseModel):
    errores: int
    vitals: int


_CORREO = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# JWT (eyJ…), códigos de un solo uso (kcpr_, kcin_) y otros secretos con prefijo conocido.
_SECRETO = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+|\bkc[a-z]{2}_[\w-]{6,}")
_ID_TICKET = re.compile(r"/KC-\d+(?=/|$)", re.IGNORECASE)


def limpiar(texto: str) -> str:
    """Sin correos ni tokens en los registros (pueden venir en el mensaje de un error)."""
    return _SECRETO.sub("[secreto]", _CORREO.sub("[correo]", texto))


def ruta_generica(ruta: str) -> str:
    return _ID_TICKET.sub("/:numero", ruta.split("?")[0])


def _identidad(authorization: str | None) -> tuple[str, str] | None:
    """(tenant, persona) si el token es válido; si no, el envío es anónimo (no se rechaza)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        claims = validar_claims_usuario(verificar_token(authorization.split(" ", 1)[1], "user"))
    except Exception:
        return None
    return claims.org, claims.sub


@router.post(
    "/client",
    status_code=202,
    response_model=TelemetriaAceptadaOut,
    summary="Recibir errores de cliente agrupados y Web Vitals del portal",
)
async def recibir(
    cuerpo: TelemetriaClienteIn,
    request: Request,
    authorization: str | None = Header(default=None),
) -> TelemetriaAceptadaOut:
    identidad = _identidad(authorization)
    if identidad:
        tenant_id, user_id = identidad
        await rl_telemetria(clave_tenant(tenant_id, "rl", "tel", user_id))
    else:
        tenant_id = user_id = None
        await rl_telemetria(f"rl:tel:ip:{obtener_ip_real(request)}")

    por_tipo: dict[str, int] = {}
    for e in cuerpo.errores:
        por_tipo[e.tipo] = por_tipo.get(e.tipo, 0) + e.veces
        log.warning(
            "portal.error_cliente",
            firma=e.firma,
            tipo=e.tipo,
            mensaje=limpiar(e.mensaje),
            ruta=ruta_generica(e.ruta),
            veces=e.veces,
            trace_id_api=e.trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    await registrar_portal(
        por_tipo,
        [(v.nombre, v.calificacion) for v in cuerpo.vitals],
        redis=getattr(request.state, "redis_metricas", None),
    )
    return TelemetriaAceptadaOut(errores=len(cuerpo.errores), vitals=len(cuerpo.vitals))
