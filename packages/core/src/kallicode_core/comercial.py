"""Validación comercial: umbrales de uso por plan."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auditoria import registrar_evento
from .config import get_settings
from .db import uno
from .errors import AppError
from .ids import nuevo
from .logging import log

_redis = None


def redis_cliente() -> Any:
    global _redis

    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            password=settings.redis_password,
            decode_responses=True,
        )

    return _redis


async def ciclo_de(
    db: AsyncSession,
    tenant_id: str,
    momento: datetime | date | None = None,
) -> str:
    """Devuelve el ciclo de facturación del tenant en `momento` (YYYY-MM),
    delegando íntegramente en PostgreSQL (core.obtener_ciclo_cuota_actual)."""
    fila = await uno(
        db,
        """
        SELECT left(
            core.obtener_ciclo_cuota_actual(
                CAST(:t AS text),
                coalesce(CAST(:m AS timestamptz)::date, current_date)
            ),
            7
        ) AS ciclo
        """,
        {"t": tenant_id, "m": momento},
    )
    return str(fila["ciclo"]) if fila and fila["ciclo"] else ""


async def estado_cuota_loc(
    db: AsyncSession,
    tenant_id: str,
    ciclo: str | None = None,
) -> dict:
    """Devuelve consumidas, límite, porcentaje y umbral del ciclo del tenant."""
    ciclo_objetivo = ciclo or await ciclo_de(db, tenant_id)
    fila = await uno(
        db,
        """
        SELECT coalesce(c.loc_consumidas, 0) AS consumidas,
               p.loc_mes AS limite,
               coalesce(c.umbral, 'normal') AS umbral
          FROM core.subscriptions s
          JOIN core.plans p ON p.codigo = s.plan_codigo
          LEFT JOIN core.usage_cycles c
                 ON c.tenant_id = s.tenant_id AND c.ciclo = :ciclo
         WHERE s.tenant_id = :t AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id, "ciclo": ciclo_objetivo},
    )

    if fila is None:
        return {
            "consumidas": 0,
            "limite": 0,
            "porcentaje": 100.0,
            "umbral": "agotado",
        }

    limite = fila["limite"]
    porcentaje = 0.0 if not limite else round(fila["consumidas"] * 100.0 / limite, 1)

    return {
        "consumidas": fila["consumidas"],
        "limite": limite,
        "porcentaje": porcentaje,
        "umbral": fila["umbral"],
    }


async def cuota_agotada(db: AsyncSession, tenant_id: str) -> bool:
    estado = await estado_cuota_loc(db, tenant_id)
    return estado["umbral"] == "agotado"


async def verificar_cupo_usuarios(
    db: AsyncSession,
    tenant_id: str,
) -> None:
    """Comprueba el cupo de usuarios e invitaciones del plan."""
    fila = await uno(
        db,
        """
        SELECT p.max_usuarios AS limite,
               (
                   SELECT count(*)
                     FROM core.users u
                    WHERE u.tenant_id = :t
                      AND u.estado IN ('activo', 'invitado')
               ) + (
                   SELECT count(*)
                     FROM core.invitations i
                    WHERE i.tenant_id = :t
                      AND i.estado = 'enviada'
               ) AS ocupados
          FROM core.subscriptions s
          JOIN core.plans p ON p.codigo = s.plan_codigo
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
            f"Tu organización alcanzó el máximo de usuarios del plan "
            f"({fila['limite']}). Amplía el plan para invitar más.",
            {"limite": fila["limite"]},
        )


async def bloquear_storage(
    db: AsyncSession,
    tenant_id: str,
) -> None:
    """Serializa reservas y confirmaciones del mismo tenant."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:t, 3))"),
        {"t": tenant_id},
    )


async def verificar_storage(
    db: AsyncSession,
    tenant_id: str,
    bytes_nuevos: int,
) -> dict | None:
    """Comprueba QU-3 y devuelve aviso al alcanzar el 80 %.

    En confirmación se actualiza primero el tamaño real y se llama
    con bytes_nuevos=0 para evitar contabilizarlo dos veces.
    """
    fila = await uno(
        db,
        """
        SELECT p.storage_mb AS limite_mb,
               coalesce(
                   (
                       SELECT sum(tamano_bytes)
                         FROM core.ticket_attachments
                        WHERE tenant_id = :t
                   ),
                   0
               ) AS usados
          FROM core.subscriptions s
          JOIN core.plans p ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id},
    )

    if not fila or fila["limite_mb"] is None:
        return None

    limite = fila["limite_mb"] * 1024 * 1024
    usados = int(fila["usados"])
    proyectados = usados + bytes_nuevos

    if proyectados >= limite:
        raise AppError(
            "ALMACENAMIENTO_AGOTADO",
            402,
            "Tu organización alcanzó el límite de almacenamiento del plan.",
            {
                "limite_mb": fila["limite_mb"],
                "usados_bytes": usados,
            },
        )

    if proyectados * 5 < limite * 4:
        return None

    aviso = {
        "codigo": "ALMACENAMIENTO_AVISO",
        "porcentaje": round(proyectados * 100 / limite, 1),
    }

    log.warning(
        "comercial.storage_aviso",
        tenant=tenant_id,
        **aviso,
    )

    notificaciones = await db.execute(
        text("""
            INSERT INTO core.notifications (
                id, tenant_id, user_id, tipo, titulo, cuerpo
            )
            SELECT
                :id || '_' || u.id,
                :t,
                u.id,
                'aviso_cuota',
                'Almacenamiento al 80 %',
                'El almacenamiento de evidencia alcanzó el 80 % del plan.'
              FROM core.users u
             WHERE u.tenant_id = :t
               AND u.estado = 'activo'
               AND u.rol IN ('owner', 'admin')
               AND NOT EXISTS (
                   SELECT 1
                     FROM core.notifications n
                    WHERE n.tenant_id = :t
                      AND n.user_id = u.id
                      AND n.titulo = 'Almacenamiento al 80 %'
                      AND n.creada_en >= current_date
               )
            RETURNING id
        """),
        {"id": nuevo("nt"), "t": tenant_id},
    )

    if notificaciones.first():
        await registrar_evento(
            db,
            tenant_id,
            evento="almacenamiento_aviso_80",
            resumen="El almacenamiento de evidencia alcanzó el 80 % del plan.",
            actor_tipo="sistema",
            datos={
                "usados_bytes": proyectados,
                "limite_bytes": limite,
                "porcentaje": aviso["porcentaje"],
            },
        )

    return aviso


async def _consumir(clave: str, limite: int):
    from . import limites

    return await limites.consumir_cubo(clave, limite)


async def rl_usuario(user_id: str, tenant_id: str):
    from . import limites

    return await limites.rl_usuario(user_id, tenant_id)


async def rl_typing(tenant_id: str, user_id: str):
    from . import limites

    return await limites.rl_typing(tenant_id, user_id)


async def rl_webhook(tenant_id: str, connection_id: str):
    from . import limites

    return await limites.rl_webhook(tenant_id, connection_id)


async def rl_login(ip: str):
    from . import limites

    return await limites.rl_login(ip)


async def plan_vigente(
    db: AsyncSession,
    tenant_id: str,
) -> dict | None:
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
          JOIN core.plans p ON p.codigo = s.plan_codigo
         WHERE s.tenant_id = :t
           AND s.finaliza_el IS NULL
        """,
        {"t": tenant_id},
    )
