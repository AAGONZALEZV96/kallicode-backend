"""Acceso a PostgreSQL con multi-tenencia RLS."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_engine_app = None
_engine_admin = None

_sessionmaker_app: async_sessionmaker[AsyncSession] | None = None
_sessionmaker_admin: async_sessionmaker[AsyncSession] | None = None

# Protege la inicialización de los engines contra condiciones de
# carrera: sin este lock, varias corrutinas concurrentes pueden ver
# _engine_app is None a la vez, crear cada una su propio par de
# engines, y las descartadas nunca se cierran (fuga de pools).
_init_lock = asyncio.Lock()


def _database_username(database_url: str) -> str | None:
    return urlparse(database_url).username


async def _init_engines() -> None:
    global _engine_app
    global _engine_admin
    global _sessionmaker_app
    global _sessionmaker_admin

    if _engine_app is not None and _engine_admin is not None:
        return

    async with _init_lock:
        # Doble chequeo: otra corrutina pudo haber terminado de
        # inicializar mientras esta esperaba el lock.
        if _engine_app is not None and _engine_admin is not None:
            return

        settings = get_settings()

        app_username = _database_username(settings.database_url)

        if app_username in {"postgres", "kallicode_admin"}:
            raise ValueError(
                "KC_DATABASE_URL debe usar el rol kallicode_app; "
                "no puede usar un usuario superusuario o BYPASSRLS"
            )

        if not settings.database_url_admin:
            raise ValueError(
                "KC_DATABASE_URL_ADMIN es obligatorio para crear el engine administrativo"
            )

        admin_username = _database_username(settings.database_url_admin)
        if not admin_username:
            raise ValueError("KC_DATABASE_URL_ADMIN debe incluir un usuario administrativo")
        if admin_username == app_username:
            raise ValueError(
                "KC_DATABASE_URL y KC_DATABASE_URL_ADMIN deben usar usuarios "
                "distintos para mantener la separación RLS"
            )

        _engine_app = create_async_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )

        _sessionmaker_app = async_sessionmaker(
            _engine_app,
            expire_on_commit=False,
        )

        _engine_admin = create_async_engine(
            settings.database_url_admin,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )

        _sessionmaker_admin = async_sessionmaker(
            _engine_admin,
            expire_on_commit=False,
        )


async def _maker_app() -> async_sessionmaker[AsyncSession]:
    await _init_engines()

    assert _sessionmaker_app is not None

    return _sessionmaker_app


async def _maker_admin() -> async_sessionmaker[AsyncSession]:
    await _init_engines()

    assert _sessionmaker_admin is not None

    return _sessionmaker_admin


@asynccontextmanager
async def sesion_tenant(
    tenant_id: str,
    actor: str = "sistema",
) -> AsyncIterator[AsyncSession]:
    """Sesión transaccional confinada al tenant mediante RLS."""

    maker = await _maker_app()

    async with maker() as db, db.begin():
        await db.execute(
            text("""
                SELECT set_config(
                    'app.tenant_id',
                    :tenant_id,
                    true
                )
                """),
            {"tenant_id": tenant_id},
        )

        await db.execute(
            text("""
                SELECT set_config(
                    'app.actor',
                    :actor,
                    true
                )
                """),
            {"actor": actor},
        )

        yield db


@asynccontextmanager
async def sesion_sistema() -> AsyncIterator[AsyncSession]:
    """Sesión administrativa sin confinamiento de tenant."""

    maker = await _maker_admin()

    async with maker() as db, db.begin():
        yield db


async def uno(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Ejecuta SQL y devuelve la primera fila como mapping."""

    result = await db.execute(text(sql), params or {})
    fila = result.mappings().first()

    return dict(fila) if fila is not None else None


async def todos(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict]:
    """Ejecuta SQL y devuelve todas las filas como mappings."""

    result = await db.execute(text(sql), params or {})

    return [dict(fila) for fila in result.mappings().all()]


async def valor(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Ejecuta SQL y devuelve el primer valor.

    Usa scalar_one_or_none() en vez de scalar(): si la consulta
    devuelve más de una fila por error, esto lo hace explotar de
    forma explícita en vez de descartar el resto en silencio.
    """

    result = await db.execute(text(sql), params or {})

    return result.scalar_one_or_none()
