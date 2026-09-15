"""Acceso a PostgreSQL con multi-tenencia RLS (§3.3 del diseño).

Los .sql de db/ son la fuente de verdad del esquema (D06): este módulo NO
define modelos declarativos — trabaja con SQL parametrizado vía SQLAlchemy
Core (text()), que mantiene el código honesto respecto del DDL real.

Cada transacción de negocio abre sesión con el tenant fijado:

    async with sesion_tenant(tenant_id) as db:
        await db.execute(text("..."), {...})

SET LOCAL app.tenant_id activa las políticas RLS: la sesión queda confinada
al tenant y un recurso ajeno es indistinguible de uno inexistente (404).

Para operaciones de sistema (webhooks antes de resolver tenant, workers
multi-tenant) se usa sesion_sistema() con el usuario admin (BYPASSRLS).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings


# ============================================================
# Motores y creadores de sesión separados
# ============================================================

_engine_app = None
_maker_app: async_sessionmaker[AsyncSession] | None = None

_engine_admin = None
_maker_admin: async_sessionmaker[AsyncSession] | None = None


def init_engines():
    """Inicializa ambos motores de base de datos de forma perezosa (singleton)."""

    global _engine_app, _maker_app, _engine_admin, _maker_admin

    if _engine_app is None:
        s = get_settings()

        # ----------------------------------------------------
        # 1. Motor de aplicación
        # Usuario: kallicode_app
        # Sujeto a RLS
        # ----------------------------------------------------

        _engine_app = create_async_engine(
            s.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )

        _maker_app = async_sessionmaker(
            _engine_app,
            expire_on_commit=False,
        )

        # ----------------------------------------------------
        # 2. Motor de sistema
        # Usuario: administrativo
        # BYPASSRLS
        # ----------------------------------------------------

        admin_url = s.database_url_admin

        if not admin_url:
            raise RuntimeError(
                "KC_DATABASE_URL_ADMIN es obligatoria para "
                "inicializar la sesión de sistema."
            )

        _engine_admin = create_async_engine(
            admin_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )

        _maker_admin = async_sessionmaker(
            _engine_admin,
            expire_on_commit=False,
        )


def get_maker_app() -> async_sessionmaker[AsyncSession]:
    """Devuelve el creador de sesiones del usuario de aplicación."""

    init_engines()

    assert _maker_app is not None

    return _maker_app


def get_maker_admin() -> async_sessionmaker[AsyncSession]:
    """Devuelve el creador de sesiones del usuario administrativo."""

    init_engines()

    assert _maker_admin is not None

    return _maker_admin


@asynccontextmanager
async def sesion_tenant(
    tenant_id: str,
    actor: str = "sistema",
) -> AsyncIterator[AsyncSession]:
    """Sesión transaccional confinada al tenant mediante RLS.

    Parámetros:
        tenant_id:
            ID de la organización (claim `org` del JWT).

        actor:
            Identidad para el historial.
            Formatos esperados:
            user:<id> | svc:<agente> | sistema

            Los triggers lo leen mediante current_setting('app.actor').
    """

    async with get_maker_app()() as db, db.begin():

        # Fija el tenant solamente durante esta transacción.
        await db.execute(
            text(
                "SELECT set_config('app.tenant_id', :t, true)"
            ),
            {"t": tenant_id},
        )

        # Fija el actor solamente durante esta transacción.
        await db.execute(
            text(
                "SELECT set_config('app.actor', :a, true)"
            ),
            {"a": actor},
        )

        yield db


@asynccontextmanager
async def sesion_sistema() -> AsyncIterator[AsyncSession]:
    """Sesión sin confinamiento de tenant.

    Utiliza exclusivamente KC_DATABASE_URL_ADMIN.

    Se utiliza para:
    - recepción de webhooks antes de resolver tenant
    - workers multi-tenant
    - billing_cycle
    - housekeeping
    - operaciones de salud/sistema
    """

    async with get_maker_admin()() as db, db.begin():
        yield db


async def uno(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> Any | None:
    """Ejecuta SQL y devuelve la primera fila como mapping (o None)."""

    r = await db.execute(
        text(sql),
        params or {},
    )

    fila = r.mappings().first()

    return dict(fila) if fila else None


async def todos(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict]:
    """Ejecuta SQL y devuelve todas las filas como mappings."""

    r = await db.execute(
        text(sql),
        params or {},
    )

    return [dict(f) for f in r.mappings().all()]


async def valor(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Ejecuta SQL y devuelve el primer valor de la primera fila."""

    r = await db.execute(
        text(sql),
        params or {},
    )

    return r.scalar()