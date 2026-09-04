"""Validación comercial: umbrales de uso por plan (§3.7 del diseño).

Reglas implementadas (los valores por defecto viven en core.plans y KC_*):

  QU-1 Cuota LOC mensual  : <80% normal · >=80% aviso · 100% tickets a cola.
  QU-2 Líneas concurrentes: la garantiza la transacción de asignación (§17.2)
                            + verificación aquí para lecturas.
  QU-3 Almacenamiento     : evidencia/adjuntos y documentación por plan.
  Cupo de usuarios        : activos + invitaciones pendientes < límite.
  RL-1/2/3 Rate limits    : ventana fija por minuto en Redis.

Toda decisión comercial se registra en el log central; el encolado por cuota
además queda en auditoría (nunca es silenciosa).
"""
from __future__ import annotations

from datetime import date

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .errors import AppError, cuota_loc_agotada, rate_limit
from .db import uno, valor
from .logging import log

_redis = None


def redis_cliente():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def ciclo_actual() -> str:
    return date.today().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# QU-1 · Cuota de LOC
# ---------------------------------------------------------------------------
async def estado_cuota_loc(db: AsyncSession, tenant_id: str) -> dict:
    """Devuelve {consumidas, limite, porcentaje, umbral} del ciclo en curso.

    Fuente: core.usage_cycles (mantenida por el trigger T6) + core.plans.
    limite=None => Enterprise sin límite (umbral siempre 'normal').
    """
    fila = await uno(db, """
        SELECT coalesce(c.loc_consumidas, 0) AS consumidas,
               p.loc_mes AS limite,
               coalesce(c.umbral, 'normal') AS umbral
          FROM core.subscriptions s
          JOIN core.plans p ON p.codigo = s.plan_codigo
          LEFT JOIN core.usage_cycles c
                 ON c.tenant_id = s.tenant_id AND c.ciclo = :ciclo
         WHERE s.tenant_id = :t AND s.finaliza_el IS NULL
    """, {"t": tenant_id, "ciclo": ciclo_actual()})
    if fila is None:
        # sin suscripción vigente: se trata como agotado (no debería ocurrir)
        return {"consumidas": 0, "limite": 0, "porcentaje": 100.0, "umbral": "agotado"}
    limite = fila["limite"]
    pct = 0.0 if not limite else round(fila["consumidas"] * 100.0 / limite, 1)
    return {"consumidas": fila["consumidas"], "limite": limite,
            "porcentaje": pct, "umbral": fila["umbral"]}


async def cuota_agotada(db: AsyncSession, tenant_id: str) -> bool:
    return (await estado_cuota_loc(db, tenant_id))["umbral"] == "agotado"


# ---------------------------------------------------------------------------
# Cupo de usuarios por plan
# ---------------------------------------------------------------------------
async def verificar_cupo_usuarios(db: AsyncSession, tenant_id: str) -> None:
    """Lanza LIMITE_USUARIOS_PLAN (402) si activos+invitados >= límite del plan."""
    fila = await uno(db, """
        SELECT p.max_usuarios AS limite,
               (SELECT count(*) FROM core.users u
                 WHERE u.tenant_id = :t AND u.estado IN ('activo','invitado')) +
               (SELECT count(*) FROM core.invitations i
                 WHERE i.tenant_id = :t AND i.estado = 'enviada') AS ocupados
          FROM core.subscriptions s JOIN core.plans p ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t AND s.finaliza_el IS NULL
    """, {"t": tenant_id})
    if fila and fila["limite"] is not None and fila["ocupados"] >= fila["limite"]:
        log.warning("comercial.limite_usuarios", tenant=tenant_id,
                    ocupados=fila["ocupados"], limite=fila["limite"])
        raise AppError("LIMITE_USUARIOS_PLAN", 402,
                       f"Tu organización alcanzó el máximo de usuarios del plan "
                       f"({fila['limite']}). Amplía el plan para invitar más.",
                       {"limite": fila["limite"]})


# ---------------------------------------------------------------------------
# QU-3 · Almacenamiento
# ---------------------------------------------------------------------------
async def verificar_storage(db: AsyncSession, tenant_id: str, bytes_nuevos: int) -> None:
    """Lanza ALMACENAMIENTO_AGOTADO (402) si la subida excede la cuota del plan."""
    fila = await uno(db, """
        SELECT p.storage_mb AS limite_mb,
               coalesce((SELECT sum(tamano_bytes) FROM core.ticket_attachments
                          WHERE tenant_id = :t), 0) AS usados
          FROM core.subscriptions s JOIN core.plans p ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t AND s.finaliza_el IS NULL
    """, {"t": tenant_id})
    if fila and fila["limite_mb"] is not None:
        if fila["usados"] + bytes_nuevos > fila["limite_mb"] * 1024 * 1024:
            raise AppError("ALMACENAMIENTO_AGOTADO", 402,
                           "Tu organización alcanzó el límite de almacenamiento "
                           "de evidencia del plan.",
                           {"limite_mb": fila["limite_mb"]})


# ---------------------------------------------------------------------------
# Rate limiting (ventana fija de 60 s en Redis)
# ---------------------------------------------------------------------------
async def _consumir(clave: str, limite: int) -> None:
    r = redis_cliente()
    n = await r.incr(clave)
    if n == 1:
        await r.expire(clave, 60)
    if n > limite:
        ttl = await r.ttl(clave)
        raise rate_limit(max(ttl, 1))


async def rl_usuario(user_id: str, tenant_id: str) -> None:
    """RL-1: 120 req/min por usuario y 1.200 por tenant."""
    s = get_settings()
    await _consumir(f"rl:u:{user_id}", s.rate_limit_usuario)
    await _consumir(f"rl:t:{tenant_id}", s.rate_limit_tenant)


async def rl_typing(user_id: str) -> None:
    """RL-2: endpoints de tipeo en vivo (dedup/impact preview)."""
    await _consumir(f"rl:typ:{user_id}", get_settings().rate_limit_typing)


async def rl_webhook(connection_id: str) -> None:
    """RL-3: 120 eventos/min por conexión."""
    await _consumir(f"rl:wh:{connection_id}", get_settings().rate_limit_webhook)


async def rl_login(ip: str) -> None:
    await _consumir(f"rl:login:{ip}", 10)


# ---------------------------------------------------------------------------
# Función-plan del plan vigente (para pantallas y checks de features)
# ---------------------------------------------------------------------------
async def plan_vigente(db: AsyncSession, tenant_id: str) -> dict | None:
    return await uno(db, """
        SELECT p.codigo, p.nombre, p.lineas, p.loc_mes, p.max_usuarios,
               p.storage_mb, p.docs_mb, p.permite_help, s.renueva_el, s.estado
          FROM core.subscriptions s JOIN core.plans p ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t AND s.finaliza_el IS NULL
    """, {"t": tenant_id})
