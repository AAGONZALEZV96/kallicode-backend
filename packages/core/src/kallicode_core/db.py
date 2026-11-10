"""Acceso a PostgreSQL con multi-tenencia RLS."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_engine_app = None
_engine_admin = None

_sessionmaker_app: async_sessionmaker[AsyncSession] | None = None
_sessionmaker_admin: async_sessionmaker[AsyncSession] | None = None
# Se crea solo al primer sesion_worker(): la API no necesita KC_DATABASE_URL_WORKER.
_sessionmaker_worker: async_sessionmaker[AsyncSession] | None = None

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


async def _maker_worker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker_worker

    if _sessionmaker_worker is not None:
        return _sessionmaker_worker

    async with _init_lock:
        if _sessionmaker_worker is None:
            settings = get_settings()
            if not settings.database_url_worker:
                raise ValueError("KC_DATABASE_URL_WORKER es obligatorio para sesion_worker()")
            if _database_username(settings.database_url_worker) in {"postgres", "kallicode_admin"}:
                raise ValueError(
                    "KC_DATABASE_URL_WORKER debe usar el rol kallicode_worker; "
                    "no puede usar un usuario superusuario o BYPASSRLS"
                )
            engine = create_async_engine(
                settings.database_url_worker,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            _sessionmaker_worker = async_sessionmaker(engine, expire_on_commit=False)

    return _sessionmaker_worker


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


@asynccontextmanager
async def conexion_sistema() -> AsyncIterator[AsyncConnection]:
    """Conexión administrativa dedicada para locks de sesión de PostgreSQL."""
    await _init_engines()

    assert _engine_admin is not None

    async with _engine_admin.connect() as conn:
        yield conn


@asynccontextmanager
async def sesion_worker() -> AsyncIterator[AsyncSession]:
    """Sesión para los barridos cross-tenant de los workers (KC-DB-02-2).

    Conecta como kallicode_worker: ve TODOS los tenants pero solo en las
    tablas de cola (webhook_inbox, jobs, refresh_tokens, ticket_attachments,
    idempotency_keys); cualquier otra tabla responde "permission denied".
    Úsala en lugar de sesion_sistema() en el normalizador, el planificador y
    las purgas; el trabajo de negocio sigue en sesion_tenant().
    """

    maker = await _maker_worker()

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
