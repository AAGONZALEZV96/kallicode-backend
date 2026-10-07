"""Pruebas de extract_schema con salvaguardas (KC-CM-04-3, CM-F13).

Un criterio de aceptacion por prueba: solo lectura, multi-esquema con lista
de exclusion, timeout tipificado sin conexion colgada y pool acotado con log.
Requieren Postgres (make up); sin el se omiten, igual que test_rls_cm.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from kallicode_codemapping import extract_sql
from kallicode_codemapping.config import Settings
from kallicode_codemapping.extract_sql import (
    SchemaExtractionTimeout,
    build_client_engine,
    extract_schema,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

CLIENT_URL = os.environ.get(
    "CM_TEST_CLIENT_DB_URL",
    f"postgresql+psycopg://postgres:{os.environ.get('KC_DB_PASSWORD', 'dev')}"
    "@localhost:5432/kallicode",
)
ESQUEMAS = ("t043_a", "t043_b", "t043_c", "t043_excluido")


def _db_disponible() -> bool:
    try:
        create_engine(CLIENT_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_disponible(), reason="Postgres no disponible (make up && make db)"
)


def _settings(**overrides: Any) -> Settings:
    return Settings(tenant_id="org_test_cm043", database_url=CLIENT_URL, **overrides)


@pytest.fixture
def base_cliente() -> Iterator[None]:
    """Cuatro esquemas: a, b y c con tablas y FK entre esquemas; uno a excluir."""
    admin = create_engine(CLIENT_URL)

    def limpiar() -> None:
        with admin.begin() as db:
            for s in ESQUEMAS:
                db.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))

    limpiar()
    with admin.begin() as db:
        for s in ESQUEMAS:
            db.execute(text(f"CREATE SCHEMA {s}"))
        db.execute(text("CREATE TABLE t043_a.clientes (id int PRIMARY KEY, nombre text)"))
        db.execute(
            text(
                "CREATE TABLE t043_b.pedidos (id int PRIMARY KEY, "
                "cliente_id int REFERENCES t043_a.clientes(id), total numeric NOT NULL)"
            )
        )
        db.execute(text("CREATE TABLE t043_c.items (id int PRIMARY KEY, pedido_id int)"))
        db.execute(text("CREATE TABLE t043_excluido.secreta (id int PRIMARY KEY)"))
    yield
    limpiar()
    admin.dispose()


def test_insert_desde_engine_del_cliente_falla_por_solo_lectura(base_cliente: None) -> None:
    engine = build_client_engine(_settings())
    try:
        with pytest.raises(DBAPIError, match="read-only transaction"), engine.begin() as db:
            db.execute(text("INSERT INTO t043_a.clientes (id, nombre) VALUES (1, 'x')"))
        with engine.connect() as db:
            assert db.execute(text("SELECT count(*) FROM t043_a.clientes")).scalar_one() == 0
    finally:
        engine.dispose()


def test_recupera_tres_esquemas_y_respeta_lista_de_exclusion(base_cliente: None) -> None:
    tables, fks = extract_schema(_settings(client_db_exclude_schemas=["t043_excluido"]))

    por_esquema = {(t["esquema"], t["name"]): t for t in tables}
    assert ("t043_a", "clientes") in por_esquema
    assert ("t043_b", "pedidos") in por_esquema
    assert ("t043_c", "items") in por_esquema
    assert not any(e == "t043_excluido" for (e, _n) in por_esquema)

    columnas = {c["name"]: c for c in por_esquema[("t043_b", "pedidos")]["columns"]}
    assert set(columnas) == {"id", "cliente_id", "total"}
    assert columnas["total"]["nullable"] is False

    fk = next(f for f in fks if f["from_table"] == "pedidos")
    assert (fk["from_schema"], fk["to_schema"], fk["to_table"]) == ("t043_b", "t043_a", "clientes")
    assert fk["from_cols"] == ["cliente_id"] and fk["to_cols"] == ["id"]


def test_timeout_produce_error_tipificado_sin_conexion_colgada(
    base_cliente: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def consulta_lenta(insp: Any, excluded: set[str]) -> None:
        insp.bind.execute(text("SELECT pg_sleep(5) /* t043_lenta */"))

    monkeypatch.setattr(extract_sql, "_read_schemas", consulta_lenta)

    with pytest.raises(SchemaExtractionTimeout):
        extract_schema(_settings(client_db_statement_timeout_ms=300))

    admin = create_engine(CLIENT_URL)
    with admin.connect() as db:
        colgadas = db.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE query LIKE '%t043_lenta%' AND pid <> pg_backend_pid() "
                "AND state <> 'idle'"
            )
        ).scalar_one()
    admin.dispose()
    assert colgadas == 0


def test_conexiones_acotadas_por_configuracion_y_registradas_en_log(
    base_cliente: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    registros: list[dict[str, Any]] = []

    class _Logger:
        def info(self, msg: str, extra: dict[str, Any] | None = None) -> None:
            registros.append({"msg": msg, **(extra or {})})

    monkeypatch.setattr(extract_sql, "logger", _Logger())

    engine = build_client_engine(_settings(client_db_max_connections=2))
    try:
        assert engine.pool.size() == 2  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 0  # type: ignore[attr-defined]
    finally:
        engine.dispose()

    extract_schema(_settings(client_db_max_connections=3))
    assert registros[-1]["max_connections"] == 3
    assert registros[-1]["phase"] == "3/7"
