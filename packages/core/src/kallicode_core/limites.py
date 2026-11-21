"""Límites de peticiones con cubo de fichas (token bucket) atómico en Redis (KC-BE-07-6)."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .config import get_settings
from .errors import rate_limit
from .redis_keys import clave_tenant

VENTANA_BASE_S: float = 60.0

# Script Lua atómico en Redis para cubo de fichas con recarga continua.
# KEYS[1]: clave del cubo
# ARGV[1]: capacidad máxima de fichas (límite)
# ARGV[2]: ventana en segundos (tasa = capacidad / ventana)
# ARGV[3]: marca de tiempo actual en segundos (float)
# ARGV[4]: fichas a consumir (por defecto 1)
# ARGV[5]: TTL de expiración en milisegundos
# Retorna: {permitido (0/1), capacidad, restante_entero, reinicio_s, retry_after_s}
SCRIPT_CUBO_FICHAS = """
local clave = KEYS[1]
local capacidad = tonumber(ARGV[1])
local ventana = tonumber(ARGV2)
local ahora = tonumber(ARGV[3])
local costo = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])

local tasa = capacidad / ventana
local estado = redis.call('HMGET', clave, 'tokens', 'ts')
local tokens = tonumber(estado[1])
local ultimo_ts = tonumber(estado[2])

if tokens == nil or ultimo_ts == nil then
    tokens = capacidad
    ultimo_ts = ahora
else
    local transcurrido = math.max(0, ahora - ultimo_ts)
    tokens = math.min(capacidad, tokens + transcurrido * tasa)
end

local permitido = 0
local retry_after = 0

if tokens >= costo then
    tokens = tokens - costo
    permitido = 1
else
    local deficit = costo - tokens
    retry_after = math.max(1, math.ceil(deficit / tasa))
end

local restante = math.max(0, math.floor(tokens + 1e-9))
local reinicio_s = 0
if tokens < capacidad then
    reinicio_s = math.max(1, math.ceil((capacidad - tokens) / tasa))
end

redis.call('HSET', clave, 'tokens', tostring(tokens), 'ts', tostring(ahora))
redis.call('PEXPIRE', clave, ttl_ms)

return {permitido, capacidad, restante, reinicio_s, retry_after}
"""


@dataclass(frozen=True)
class EstadoLimite:
    """Resultado de evaluar una ficha en un cubo de fichas."""

    nombre: str
    limite: int
    restante: int
    reinicio_s: int
    permitido: bool
    retry_after_s: int = 0

    def cabeceras(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limite),
            "X-RateLimit-Remaining": str(self.restante),
            "X-RateLimit-Reset": str(self.reinicio_s),
        }


_estados_peticion: ContextVar[list[EstadoLimite] | None] = ContextVar(
    "kc_rate_limit_estados",
    default=None,
)


def ahora_segundos() -> float:
    """Reloj en segundos (aislado para facilitar pruebas deterministas)."""
    return time.time()


def iniciar_contexto_request() -> None:
    """Reinicia el acumulador de límites evaluados durante la petición HTTP actual."""
    _estados_peticion.set([])


def registrar_estado(estado: EstadoLimite) -> None:
    """Registra el estado de límite evaluado en la petición HTTP actual."""
    actuales = _estados_peticion.get()
    if actuales is None:
        actuales = []
        _estados_peticion.set(actuales)
    actuales.append(estado)


def obtener_estados_request() -> list[EstadoLimite]:
    """Devuelve los estados de límite evaluados en la petición HTTP actual."""
    actuales = _estados_peticion.get()
    return list(actuales) if actuales else []


def cabeceras_desde_estados(estados: Sequence[EstadoLimite]) -> dict[str, str]:
    """Construye las cabeceras X-RateLimit-* a partir de los cubos evaluados.

    - Si se evaluó un límite específico (typing, webhook, login), prevalece ese cubo.
    - Si se evaluó RL-1 (usuario + tenant), prevalece el más restrictivo (menor restante)
      y se exponen además las cabeceras desglosadas de usuario y tenant.
    """
    if not estados:
        return {}

    por_nombre: dict[str, EstadoLimite] = {}
    for st in estados:
        por_nombre[st.nombre] = st

    # Si hubo un rechazo, priorizar el cubo que rechazó la petición
    rechazados = [st for st in estados if not st.permitido]
    if rechazados:
        principal = rechazados[-1]
    else:
        especificos = [por_nombre[n] for n in ("typing", "webhook", "login") if n in por_nombre]
        if especificos:
            principal = especificos[-1]
        elif "usuario" in por_nombre and "tenant" in por_nombre:
            u = por_nombre["usuario"]
            t = por_nombre["tenant"]
            principal = u if u.restante <= t.restante else t
        else:
            principal = estados[-1]

    headers = principal.cabeceras()

    if "usuario" in por_nombre:
        u = por_nombre["usuario"]
        headers["X-RateLimit-Limit-Usuario"] = str(u.limite)
        headers["X-RateLimit-Remaining-Usuario"] = str(u.restante)
        headers["X-RateLimit-Reset-Usuario"] = str(u.reinicio_s)

    if "tenant" in por_nombre:
        t = por_nombre["tenant"]
        headers["X-RateLimit-Limit-Tenant"] = str(t.limite)
        headers["X-RateLimit-Remaining-Tenant"] = str(t.restante)
        headers["X-RateLimit-Reset-Tenant"] = str(t.reinicio_s)

    return headers


def cabeceras_por_defecto() -> dict[str, str]:
    """Cabeceras base del catálogo RL-1 cuando una ruta no autenticada no consumió fichas."""
    settings = get_settings()
    limite_u = settings.rate_limit_usuario
    limite_t = settings.rate_limit_tenant
    return {
        "X-RateLimit-Limit": str(limite_u),
        "X-RateLimit-Remaining": str(limite_u),
        "X-RateLimit-Reset": "0",
        "X-RateLimit-Limit-Usuario": str(limite_u),
        "X-RateLimit-Remaining-Usuario": str(limite_u),
        "X-RateLimit-Reset-Usuario": "0",
        "X-RateLimit-Limit-Tenant": str(limite_t),
        "X-RateLimit-Remaining-Tenant": str(limite_t),
        "X-RateLimit-Reset-Tenant": "0",
    }


def cabeceras_contexto_actual(*, incluir_defecto: bool = False) -> dict[str, str]:
    """Obtiene las cabeceras X-RateLimit-* para la respuesta HTTP actual."""
    estados = obtener_estados_request()
    if estados:
        return cabeceras_desde_estados(estados)
    return cabeceras_por_defecto() if incluir_defecto else {}


def _lanzar_exceso(estado: EstadoLimite, *, extra_headers: dict[str, str] | None = None) -> None:
    headers = cabeceras_contexto_actual()
    if not headers:
        headers = estado.cabeceras()
    if extra_headers:
        headers.update(extra_headers)
    err = rate_limit(max(estado.retry_after_s, 1))
    if err.detalle is None:
        err.detalle = {}
    err.detalle["_rate_limit_headers"] = headers
    raise err


async def consumir_cubo(
    clave: str,
    capacidad: int,
    *,
    ventana_s: float = VENTANA_BASE_S,
    costo: int = 1,
    nombre: str = "general",
    cliente_redis: Any = None,
    reloj: Callable[[], float] | None = None,
    lanzar_si_excede: bool = True,
) -> EstadoLimite:
    """Consume fichas de un cubo en Redis de forma atómica vía script Lua."""
    if cliente_redis is None:
        from .comercial import redis_cliente

        cliente_redis = redis_cliente()

    ahora = (reloj or ahora_segundos)()
    ttl_ms = max(1000, math.ceil(ventana_s * 1000 * 2))

    crudo = await cliente_redis.eval(
        SCRIPT_CUBO_FICHAS,
        1,
        clave,
        capacidad,
        ventana_s,
        ahora,
        costo,
        ttl_ms,
    )

    permitido = bool(int(crudo[0]))
    limite_res = int(crudo[1])
    restante = int(crudo[2])
    reinicio_s = int(crudo[3])
    retry_after_s = int(crudo[4])

    estado = EstadoLimite(
        nombre=nombre,
        limite=limite_res,
        restante=restante,
        reinicio_s=reinicio_s,
        permitido=permitido,
        retry_after_s=retry_after_s,
    )
    registrar_estado(estado)

    if not permitido and lanzar_si_excede:
        _lanzar_exceso(estado)

    return estado


async def rl_usuario(
    user_id: str,
    tenant_id: str,
    *,
    cliente_redis: Any = None,
) -> tuple[EstadoLimite, EstadoLimite]:
    """RL-1: Límite compuesto por usuario y por tenant."""
    settings = get_settings()

    estado_usuario = await consumir_cubo(
        clave_tenant(tenant_id, "rl", "u", user_id),
        settings.rate_limit_usuario,
        nombre="usuario",
        cliente_redis=cliente_redis,
    )
    estado_tenant = await consumir_cubo(
        clave_tenant(tenant_id, "rl", "t"),
        settings.rate_limit_tenant,
        nombre="tenant",
        cliente_redis=cliente_redis,
    )
    return estado_usuario, estado_tenant


async def rl_typing(
    tenant_id: str,
    user_id: str,
    *,
    cliente_redis: Any = None,
) -> EstadoLimite:
    """RL-2: Límite de tipeo en vivo (dedup-preview / impact-preview)."""
    return await consumir_cubo(
        clave_tenant(tenant_id, "rl", "typ", user_id),
        get_settings().rate_limit_typing,
        nombre="typing",
        cliente_redis=cliente_redis,
    )


async def rl_webhook(
    tenant_id: str,
    connection_id: str,
    *,
    cliente_redis: Any = None,
) -> EstadoLimite:
    """RL-3: Límite de recepción de webhooks por conexión."""
    return await consumir_cubo(
        clave_tenant(tenant_id, "rl", "wh", connection_id),
        get_settings().rate_limit_webhook,
        nombre="webhook",
        cliente_redis=cliente_redis,
    )


async def rl_login(
    ip: str,
    *,
    cliente_redis: Any = None,
) -> EstadoLimite:
    """Límite de intentos de login por IP."""
    return await consumir_cubo(
        f"rl:login:{ip}",
        get_settings().rate_limit_login,
        nombre="login",
        cliente_redis=cliente_redis,
    )
