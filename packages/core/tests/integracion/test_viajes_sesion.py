"""Viajes a la base por petición y reintento sin pre-ping (KC-CM-11-6), contra PostgreSQL real.

Antes cada petición hacía 8 viajes (9 en el grafo): 3 del pre-ping (BEGIN; ; ROLLBACK), BEGIN,
dos set_config, statement_timeout, la consulta y COMMIT. Ahora son 4: BEGIN, un set_config con
tenant, actor y tiempo, la consulta y COMMIT. Los viajes se cuentan en el driver (asyncpg), que
es donde se pagan.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import asyncpg
import psycopg
import pytest
from asyncpg.prepared_stmt import PreparedStatement
from kallicode_core import db
from kallicode_core.cm_grafo_sesion import ConsultaGrafoSesion
from kallicode_core.cm_impacto import TIEMPO_MAXIMO_MS, ConsultaGrafoIn, ResolverNodosIn
from sqlalchemy import text

pytestmark = [pytest.mark.integracion, pytest.mark.asyncio]

TENANT = "org_test_viajes_sesion"


@pytest.fixture
def viajes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Registra cada viaje del driver: sentencias simples y ejecuciones de preparadas."""
    registro: list[str] = []
    original_execute = asyncpg.connection.Connection.execute
    original_bind = PreparedStatement._PreparedStatement__bind_execute  # type: ignore[attr-defined]

    async def execute(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
        registro.append(query.strip())
        return await original_execute(self, query, *args, **kwargs)

    async def bind_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
        registro.append(self.get_query().strip())
        return await original_bind(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.connection.Connection, "execute", execute)
    monkeypatch.setattr(
        PreparedStatement, "_PreparedStatement__bind_execute", bind_execute, raising=True
    )
    yield registro


async def _calentar() -> None:
    """Deja una conexión abierta en el pool: lo que se mide es una petición normal."""
    async with db.sesion_tenant(TENANT) as s:
        await s.execute(text("SELECT 1"))


async def test_resolver_nodos_hace_cuatro_viajes(viajes: list[str]) -> None:
    await _calentar()
    viajes.clear()
    async with db.sesion_tenant(
        TENANT, actor="svc:orquestador", statement_timeout_ms=TIEMPO_MAXIMO_MS
    ) as s:
        await ConsultaGrafoSesion(s, tiempo_limitado=True).resolver_nodos(
            ResolverNodosIn(ids=["x"])
        )
    assert len(viajes) <= 4, viajes
    assert viajes[0].upper().startswith("BEGIN")
    assert viajes[-1].upper().startswith("COMMIT")
    assert not any(v.strip(" ;") == "" for v in viajes), "quedó el pre-ping"


async def test_impacto_hace_cuatro_viajes(viajes: list[str]) -> None:
    await _calentar()
    viajes.clear()
    async with db.sesion_tenant(
        TENANT, actor="svc:orquestador", statement_timeout_ms=TIEMPO_MAXIMO_MS
    ) as s:
        await ConsultaGrafoSesion(s, tiempo_limitado=True).impacto(
            ConsultaGrafoIn(nodos=["x"], k=3)
        )
    assert len(viajes) <= 4, viajes


async def test_el_contexto_se_fija_en_un_viaje_y_es_local_a_la_transaccion() -> None:
    async with db.sesion_tenant(TENANT, actor="svc:prueba", statement_timeout_ms=1234) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT current_setting('app.tenant_id'), current_setting('app.actor'),"
                    " current_setting('statement_timeout')"
                )
            )
        ).one()
    assert tuple(fila) == (TENANT, "svc:prueba", "1234ms")

    # La siguiente transacción, en la misma conexión del pool, no hereda nada.
    async with db.sesion_tenant("org_otro_tenant") as s:
        fila = (
            await s.execute(
                text(
                    "SELECT current_setting('app.tenant_id'), current_setting('app.actor'),"
                    " current_setting('statement_timeout')"
                )
            )
        ).one()
    assert fila[0] == "org_otro_tenant"
    assert fila[1] == "sistema"
    assert fila[2] != "1234ms"


async def test_sin_statement_timeout_no_se_toca_el_tiempo() -> None:
    async with db.sesion_tenant(TENANT) as s:
        tiempo = (await s.execute(text("SELECT current_setting('statement_timeout')"))).scalar()
    assert tiempo != f"{TIEMPO_MAXIMO_MS}ms"


async def test_una_conexion_cortada_en_el_pool_se_reintenta_sin_error() -> None:
    """Sin pre-ping, la conexión rota se descubre en la primera sentencia: se reintenta una vez."""
    dsn = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not dsn:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida: hace falta un superusuario")
    await _calentar()
    # Corta todas las conexiones de la API (kallicode_app), como un reinicio de PostgreSQL. Solo
    # un superusuario puede: kallicode_admin no tiene pg_signal_backend, a propósito.
    with psycopg.connect(dsn, autocommit=True) as superusuario:
        fila = superusuario.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity"
            " WHERE usename = 'kallicode_app'"
        ).fetchone()
    assert fila and fila[0] >= 1

    async with db.sesion_tenant(TENANT, actor="svc:orquestador") as s:
        tenant = (await s.execute(text("SELECT current_setting('app.tenant_id')"))).scalar()
    assert tenant == TENANT

    # Y las siguientes también: el pool se recicló entero, no solo la conexión que falló.
    for _ in range(3):
        async with db.sesion_tenant(TENANT) as s:
            assert (await s.execute(text("SELECT 1"))).scalar() == 1


async def test_el_bloque_que_falla_hace_rollback() -> None:
    """Mismo cierre que antes: si el bloque lanza, no se confirma nada."""
    with pytest.raises(RuntimeError, match="a propósito"):
        async with db.sesion_tenant(TENANT) as s:
            await s.execute(text("CREATE TEMP TABLE viajes_rollback (x int) ON COMMIT DROP"))
            await s.execute(text("INSERT INTO viajes_rollback VALUES (1)"))
            raise RuntimeError("a propósito")
    async with db.sesion_tenant(TENANT) as s:
        existe = (await s.execute(text("SELECT to_regclass('pg_temp.viajes_rollback')"))).scalar()
    assert existe is None
