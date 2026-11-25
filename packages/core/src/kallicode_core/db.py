"""Acceso a PostgreSQL con multi-tenencia RLS."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Mapping
from collections.abc import Set as AbstractSet
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, DisconnectionError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_engine_app = None
_engine_admin = None
_urls_engines: tuple[str, str | None] | None = None

_sessionmaker_app: async_sessionmaker[AsyncSession] | None = None
_sessionmaker_admin: async_sessionmaker[AsyncSession] | None = None
# Se crea solo al primer sesion_worker(): la API no necesita KC_DATABASE_URL_WORKER.
_sessionmaker_worker: async_sessionmaker[AsyncSession] | None = None

# Protege la inicialización de los engines contra condiciones de
# carrera: sin este lock, varias corrutinas concurrentes pueden ver
# _engine_app is None a la vez, crear cada una su propio par de
# engines, y las descartadas nunca se cierran (fuga de pools).
_init_lock = asyncio.Lock()


def _descartar_cerradas(dbapi_connection: Any, _record: Any, _proxy: Any) -> None:
    """Al sacar una conexión del pool, descarta la que asyncpg ya sabe cerrada (KC-CM-11-6).

    Cuando PostgreSQL se reinicia o termina el backend, cierra el socket y asyncpg lo marca en
    local: comprobarlo no cuesta ningún viaje. DisconnectionError hace que el pool la descarte y
    entregue otra (hasta tres intentos) antes de que la petición ejecute nada. Lo que asyncpg aún
    no ha visto (un corte silencioso) lo cubre el reintento de sesion_tenant.
    """
    conexion = getattr(dbapi_connection, "_connection", None)
    if conexion is not None and conexion.is_closed():
        raise DisconnectionError("conexión del pool cerrada por el servidor")


def _database_username(database_url: str) -> str | None:
    return urlparse(database_url).username


async def _init_engines() -> None:
    global _engine_app
    global _engine_admin
    global _sessionmaker_app
    global _sessionmaker_admin
    global _urls_engines

    settings = get_settings()
    urls_actuales = (settings.database_url, settings.database_url_admin)

    if _engine_app is not None and _engine_admin is not None and _urls_engines == urls_actuales:
        return

    async with _init_lock:
        settings = get_settings()
        urls_actuales = (settings.database_url, settings.database_url_admin)
        # Doble chequeo: otra corrutina pudo haber terminado de
        # inicializar mientras esta esperaba el lock.
        if _engine_app is not None and _engine_admin is not None and _urls_engines == urls_actuales:
            return

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
            # Sin pre-ping (KC-CM-11-6): costaba 3 viajes por petición (BEGIN; ; ROLLBACK). Lo
            # sustituyen, sin viajes: la comprobación local al sacar la conexión del pool
            # (_descartar_cerradas), el reintento de la primera sentencia en sesion_tenant y
            # pool_recycle, que evita reutilizar conexiones que un NAT o el servidor cortaron por
            # inactividad.
            pool_pre_ping=False,
            pool_recycle=settings.db_pool_recycle_s,
            # asyncpg: tope al conectar y a cada sentencia. Al vencer lanza TimeoutError, que la
            # API traduce a 503 DEPENDENCIA_NO_DISPONIBLE (KC-CM-11-5).
            connect_args={
                "timeout": settings.db_connect_timeout_s,
                "command_timeout": settings.db_command_timeout_s,
            },
        )

        event.listen(_engine_app.sync_engine, "checkout", _descartar_cerradas)

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
        _urls_engines = urls_actuales


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


# Contexto de la transacción en UN viaje (KC-CM-11-6): antes eran dos set_config por separado.
# Los tres valores son locales a la transacción (is_local = true), como antes.
SQL_CONTEXTO = (
    "SELECT set_config('app.tenant_id', :tenant_id, true), set_config('app.actor', :actor, true)"
)
SQL_CONTEXTO_CON_TIEMPO = (
    SQL_CONTEXTO + ", set_config('statement_timeout', :statement_timeout, true)"
)


def _sql_contexto(
    tenant_id: str, actor: str, statement_timeout_ms: int | None
) -> tuple[str, dict[str, str]]:
    params = {"tenant_id": tenant_id, "actor": actor}
    if statement_timeout_ms is None:
        return SQL_CONTEXTO, params
    if statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms debe ser positivo")
    return SQL_CONTEXTO_CON_TIEMPO, params | {"statement_timeout": str(statement_timeout_ms)}


@asynccontextmanager
async def sesion_tenant(
    tenant_id: str,
    actor: str = "sistema",
    *,
    statement_timeout_ms: int | None = None,
) -> AsyncIterator[AsyncSession]:
    """Sesión transaccional confinada al tenant mediante RLS.

    `statement_timeout_ms` acota cada sentencia de la transacción (lo usa la capa de consulta del
    grafo). Tenant, actor y tiempo se fijan en un solo viaje.

    El motor de la API no hace pre-ping (costaba 3 viajes por petición, KC-CM-11-6). En su lugar,
    si la PRIMERA sentencia falla porque la conexión del pool estaba rota (PostgreSQL reiniciado,
    failover), SQLAlchemy la invalida, recicla las demás del pool y se reintenta una vez con otra.
    Es seguro: la petición aún no ha ejecutado nada. Cualquier otro error, o un segundo fallo,
    sale tal cual (la API lo traduce a 503 DEPENDENCIA_NO_DISPONIBLE).
    """

    maker = await _maker_app()
    sql, params = _sql_contexto(tenant_id, actor, statement_timeout_ms)

    for intento in (1, 2):
        pila = AsyncExitStack()
        try:
            db = await pila.enter_async_context(maker())
            await pila.enter_async_context(db.begin())
            await db.execute(text(sql), params)
        except DBAPIError as exc:
            # Cierra la sesión y la transacción como si hubiera fallado dentro (rollback).
            await pila.__aexit__(type(exc), exc, exc.__traceback__)
            if intento == 1 and exc.connection_invalidated:
                continue
            raise
        except BaseException as exc:
            await pila.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        break

    # Mismo cierre que `async with maker() as db, db.begin()`: commit al salir bien, rollback si
    # el bloque falla.
    async with pila:
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


# Nombre simple de tabla o columna: minúsculas, dígitos y guion bajo (sin comillas ni espacios).
_IDENTIFICADOR = re.compile(r"[a-z_][a-z0-9_]*")


def _validar_identificador(nombre: str) -> str:
    if not _IDENTIFICADOR.fullmatch(nombre):
        raise ValueError(f"Identificador SQL inválido: {nombre!r}")
    return nombre


async def actualizar(
    db: AsyncSession,
    tabla: str,
    cambios: Mapping[str, Any],
    *,
    permitidas: AbstractSet[str],
    donde: Mapping[str, Any],
) -> int:
    """UPDATE parcial (los PATCH) sin SQL armado con datos de entrada (KC-BE-07-5, cierra BE-F36).

    - Cada columna de `cambios` tiene que estar en `permitidas`: una lista fija del que
      llama, nunca derivada de la entrada. Así, un campo nuevo en un modelo Pydantic no llega
      a la base sin que alguien lo agregue a esa lista.
    - Tabla y columnas se validan además como identificadores simples, y todos los valores
      (los de `cambios` y los del filtro `donde`) van como parámetros.
    - Es un solo UPDATE con todas las columnas, y `donde` es obligatorio: nunca se actualiza
      la tabla entera.

    Sin cambios no ejecuta nada y devuelve 0; si no, devuelve las filas afectadas. Una
    columna fuera de `permitidas` o un identificador inválido es un error de programación
    (ValueError), no de la persona que llama a la API.
    """

    if not donde:
        raise ValueError("actualizar() exige un filtro en `donde`.")
    if not cambios:
        return 0
    no_permitidas = sorted(set(cambios) - set(permitidas))
    if no_permitidas:
        raise ValueError(f"Columnas no permitidas en {tabla}: {', '.join(no_permitidas)}")
    partes = tabla.split(".")
    if len(partes) > 2:
        raise ValueError(f"Identificador SQL inválido: {tabla!r}")
    for nombre in (*partes, *cambios, *donde):
        _validar_identificador(nombre)

    asignaciones = ", ".join(f"{columna} = :v_{columna}" for columna in cambios)
    filtro = " AND ".join(f"{columna} = :w_{columna}" for columna in donde)
    parametros = {f"v_{c}": v for c, v in cambios.items()} | {f"w_{c}": v for c, v in donde.items()}

    # Los identificadores salen de listas fijas y se validaron arriba; los valores van como
    # parámetros. Es el único lugar del backend que arma un UPDATE así.
    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
    sql = text(f"UPDATE {tabla} SET {asignaciones} WHERE {filtro}")
    resultado = await db.execute(sql, parametros)
    return resultado.rowcount
