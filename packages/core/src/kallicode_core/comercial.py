"""Validación comercial: umbrales de uso por plan.

Reglas implementadas:

  QU-1 Cuota LOC mensual:
      <80 % normal
      >=80 % aviso
      >=100 % agotado

  QU-2 Líneas concurrentes:
      Garantizada por la transacción de asignación y las verificaciones
      de lectura correspondientes.

  QU-3 Almacenamiento:
      Calculado desde los tamaños persistidos de adjuntos y evidencia.
      Después de confirmar un upload, el tamaño persistido corresponde
      al tamaño real informado por Blob Storage.

  Cupo de usuarios:
      Usuarios activos e invitaciones pendientes.

  Rate limits:
      Ventanas fijas mediante Redis.

Toda decisión comercial relevante se registra en el log central.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import uno
from .errors import AppError, rate_limit
from .logging import log
from .redis_keys import clave_tenant

_redis = None


def redis_cliente() -> Any:
    """Devuelve el cliente Redis compartido por el proceso."""
    global _redis

    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            password=settings.redis_password,
            decode_responses=True,
        )

    return _redis


def ciclo_actual() -> str:
    """Devuelve el ciclo comercial mensual actual."""
    return date.today().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# QU-1 · Cuota de LOC
# ---------------------------------------------------------------------------
async def estado_cuota_loc(
    db: AsyncSession,
    tenant_id: str,
) -> dict:
    """Devuelve el consumo LOC del ciclo vigente.

    La respuesta contiene:

    - consumidas
    - limite
    - porcentaje
    - umbral

    limite=None representa un plan sin límite.
    """
    fila = await uno(
        db,
        """
        SELECT coalesce(c.loc_consumidas, 0) AS consumidas,
               p.loc_mes AS limite,
               coalesce(c.umbral, 'normal') AS umbral
          FROM core.subscriptions s
          JOIN core.plans p
            ON p.codigo = s.plan_codigo
          LEFT JOIN core.usage_cycles c
            ON c.tenant_id = s.tenant_id
           AND c.ciclo = :ciclo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {
            "t": tenant_id,
            "ciclo": ciclo_actual(),
        },
    )

    if fila is None:
        return {
            "consumidas": 0,
            "limite": 0,
            "porcentaje": 100.0,
            "umbral": "agotado",
        }

    limite = fila["limite"]
    consumidas = fila["consumidas"]

    porcentaje = 0.0 if limite in (None, 0) else round(consumidas * 100.0 / limite, 1)

    return {
        "consumidas": consumidas,
        "limite": limite,
        "porcentaje": porcentaje,
        "umbral": fila["umbral"],
    }


async def cuota_agotada(
    db: AsyncSession,
    tenant_id: str,
) -> bool:
    """Indica si la cuota mensual de LOC está agotada."""
    estado = await estado_cuota_loc(db, tenant_id)

    return estado["umbral"] == "agotado"


# ---------------------------------------------------------------------------
# Cupo de usuarios
# ---------------------------------------------------------------------------
async def verificar_cupo_usuarios(
    db: AsyncSession,
    tenant_id: str,
) -> None:
    """Valida el máximo de usuarios permitido por el plan."""
    fila = await uno(
        db,
        """
        SELECT p.max_usuarios AS limite,
               (
                   SELECT count(*)
                     FROM core.users u
                    WHERE u.tenant_id = :t
                      AND u.estado IN ('activo', 'invitado')
               )
               +
               (
                   SELECT count(*)
                     FROM core.invitations i
                    WHERE i.tenant_id = :t
                      AND i.estado = 'enviada'
               ) AS ocupados
          FROM core.subscriptions s
          JOIN core.plans p
            ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id},
    )

    if fila and fila["limite"] is not None and fila["ocupados"] >= fila["limite"]:
        log.warning(
            "comercial.limite_usuarios",
            tenant=tenant_id,
            ocupados=fila["ocupados"],
            limite=fila["limite"],
        )

        raise AppError(
            "LIMITE_USUARIOS_PLAN",
            402,
            (
                "Tu organización alcanzó el máximo de usuarios del plan "
                f"({fila['limite']}). Amplía el plan para invitar más."
            ),
            {
                "limite": fila["limite"],
                "ocupados": fila["ocupados"],
            },
        )


# ---------------------------------------------------------------------------
# QU-3 · Almacenamiento
# ---------------------------------------------------------------------------
async def estado_storage(
    db: AsyncSession,
    tenant_id: str,
) -> dict:
    """Devuelve el estado actual de la cuota de almacenamiento.

    Los adjuntos rechazados continúan contando hasta que housekeeping los
    elimine, porque el blob real sigue ocupando almacenamiento durante ese
    periodo.

    Respuesta:

    {
        usados,
        limite,
        porcentaje,
        umbral
    }
    """
    fila = await uno(
        db,
        """
        SELECT p.storage_mb AS limite_mb,
               coalesce(
                   (
                       SELECT sum(a.tamano_bytes)
                         FROM core.ticket_attachments a
                        WHERE a.tenant_id = :t
                   ),
                   0
               ) AS usados
          FROM core.subscriptions s
          JOIN core.plans p
            ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id},
    )

    if fila is None:
        return {
            "usados": 0,
            "limite": 0,
            "porcentaje": 100.0,
            "umbral": "agotado",
        }

    usados = int(fila["usados"])

    limite = None if fila["limite_mb"] is None else int(fila["limite_mb"]) * 1024 * 1024

    if limite is None:
        porcentaje = 0.0
        umbral = "normal"
    elif limite == 0:
        porcentaje = 100.0
        umbral = "agotado"
    else:
        porcentaje = round(usados * 100.0 / limite, 1)

        if usados >= limite:
            umbral = "agotado"
        elif usados >= limite * 0.8:
            umbral = "aviso_80"
        else:
            umbral = "normal"

    return {
        "usados": usados,
        "limite": limite,
        "porcentaje": porcentaje,
        "umbral": umbral,
    }


async def verificar_storage(
    db: AsyncSession,
    tenant_id: str,
    bytes_nuevos: int,
) -> dict:
    """Valida una variación proyectada de almacenamiento.

    bytes_nuevos puede ser:

    - Positivo al reservar una subida nueva.
    - Positivo o negativo durante la confirmación, porque representa:
      tamaño_real - tamaño_declarado.

    Devuelve el estado proyectado de la cuota.
    """

    await db.execute(
        text("""
            SELECT pg_advisory_xact_lock(
                hashtextextended(:clave, 0)
            )
        """),
        {"clave": f"storage:{tenant_id}"},
    )

    actual = await estado_storage(db, tenant_id)

    usados_proyectados = max(
        0,
        actual["usados"] + bytes_nuevos,
    )
    limite = actual["limite"]

    if limite is not None and usados_proyectados > limite:
        log.warning(
            "comercial.storage_agotado",
            tenant=tenant_id,
            usados=actual["usados"],
            proyectados=usados_proyectados,
            limite=limite,
        )

        raise AppError(
            "ALMACENAMIENTO_AGOTADO",
            402,
            ("Tu organización alcanzó el límite de almacenamiento de evidencia del plan."),
            {
                "limite_bytes": limite,
                "usados_bytes": actual["usados"],
                "proyectados_bytes": usados_proyectados,
                "variacion_bytes": bytes_nuevos,
            },
        )

    porcentaje = 0.0 if limite in (None, 0) else round(usados_proyectados * 100.0 / limite, 1)

    if limite is None:
        umbral = "normal"
    elif usados_proyectados >= limite:
        umbral = "agotado"
    elif usados_proyectados >= limite * 0.8:
        umbral = "aviso_80"
    else:
        umbral = "normal"

    if umbral == "aviso_80":
        log.warning(
            "comercial.storage_aviso_80",
            tenant=tenant_id,
            usados=usados_proyectados,
            limite=limite,
            porcentaje=porcentaje,
        )

    return {
        "usados": usados_proyectados,
        "limite": limite,
        "porcentaje": porcentaje,
        "umbral": umbral,
    }


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
async def _consumir(
    clave: str,
    limite: int,
) -> None:
    """Consume una unidad de una ventana fija de 60 segundos."""
    redis = redis_cliente()
    cantidad = await redis.incr(clave)

    if cantidad == 1:
        await redis.expire(clave, 60)

    if cantidad > limite:
        ttl = await redis.ttl(clave)
        raise rate_limit(max(ttl, 1))


async def rl_usuario(
    user_id: str,
    tenant_id: str,
) -> None:
    """RL-1: límite por usuario y tenant."""
    settings = get_settings()

    await _consumir(
        clave_tenant(tenant_id, "rl", "u", user_id),
        settings.rate_limit_usuario,
    )
    await _consumir(
        clave_tenant(tenant_id, "rl", "t"),
        settings.rate_limit_tenant,
    )


async def rl_typing(
    tenant_id: str,
    user_id: str,
) -> None:
    """RL-2: endpoints utilizados durante escritura en vivo."""
    await _consumir(
        clave_tenant(tenant_id, "rl", "typ", user_id),
        get_settings().rate_limit_typing,
    )


async def rl_webhook(
    tenant_id: str,
    connection_id: str,
) -> None:
    """RL-3: eventos entrantes por conexión."""
    await _consumir(
        clave_tenant(
            tenant_id,
            "rl",
            "wh",
            connection_id,
        ),
        get_settings().rate_limit_webhook,
    )


async def rl_login(ip: str) -> None:
    """Límite de intentos de login por dirección IP."""
    await _consumir(
        f"rl:login:{ip}",
        10,
    )


# ---------------------------------------------------------------------------
# Plan vigente
# ---------------------------------------------------------------------------
async def plan_vigente(
    db: AsyncSession,
    tenant_id: str,
) -> dict | None:
    """Devuelve las capacidades del plan vigente."""
    return await uno(
        db,
        """
        SELECT p.codigo,
               p.nombre,
               p.lineas,
               p.loc_mes,
               p.max_usuarios,
               p.storage_mb,
               p.docs_mb,
               p.permite_help,
               s.renueva_el,
               s.estado
          FROM core.subscriptions s
          JOIN core.plans p
            ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id},
    )
