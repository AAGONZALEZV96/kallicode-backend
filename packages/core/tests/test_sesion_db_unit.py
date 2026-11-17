"""Piezas sin base de datos de la sesión RLS de la API (KC-CM-11-6)."""

from __future__ import annotations

import pytest
from kallicode_core import db
from sqlalchemy.exc import DisconnectionError


class _Asyncpg:
    def __init__(self, cerrada: bool) -> None:
        self._cerrada = cerrada

    def is_closed(self) -> bool:
        return self._cerrada


class _Adaptador:
    """Lo que el pool entrega: el adaptador de SQLAlchemy con la conexión asyncpg en `_connection`."""

    def __init__(self, cerrada: bool) -> None:
        self._connection = _Asyncpg(cerrada)


def test_una_conexion_cerrada_se_descarta_al_sacarla_del_pool() -> None:
    with pytest.raises(DisconnectionError):
        db._descartar_cerradas(_Adaptador(cerrada=True), None, None)


def test_una_conexion_abierta_se_entrega_sin_viajes() -> None:
    db._descartar_cerradas(_Adaptador(cerrada=False), None, None)


def test_un_adaptador_sin_conexion_asyncpg_no_falla() -> None:
    db._descartar_cerradas(object(), None, None)


def test_el_contexto_va_en_una_sola_sentencia() -> None:
    sql, params = db._sql_contexto("org_a", "svc:x", None)
    assert sql.count("set_config") == 2
    assert "statement_timeout" not in sql
    assert params == {"tenant_id": "org_a", "actor": "svc:x"}


def test_el_statement_timeout_se_suma_a_la_misma_sentencia() -> None:
    sql, params = db._sql_contexto("org_a", "svc:x", 5000)
    assert sql.count("set_config") == 3
    assert sql.count("SELECT") == 1
    assert params["statement_timeout"] == "5000"
    # Los tres son locales a la transacción.
    assert sql.count(", true)") == 3


@pytest.mark.parametrize("ms", [0, -1])
def test_un_statement_timeout_no_positivo_se_rechaza(ms: int) -> None:
    with pytest.raises(ValueError, match="positivo"):
        db._sql_contexto("org_a", "svc:x", ms)
