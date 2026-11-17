"""Clasificación de caídas de dependencias en errores del catálogo (KC-CM-11-5)."""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from kallicode_core.errors import (
    SERVICIO_BASE_DATOS,
    AppError,
    dependencia_no_disponible,
    es_caida_base_datos,
    fallo_de_dependencia,
)
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Un puerto en el que no escucha nadie: la conexión se rechaza al instante.
DSN_CERRADO = "postgresql://u:p@127.0.0.1:1/x"


def test_asyncpg_al_conectar_lanza_el_error_del_sistema_y_se_reconoce() -> None:
    """La ruta real de la API: asyncpg no envuelve el ConnectionRefusedError."""

    async def _conectar() -> BaseException:
        motor = create_async_engine(DSN_CERRADO.replace("postgresql", "postgresql+asyncpg"))
        try:
            async with motor.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return exc
        finally:
            await motor.dispose()
        raise AssertionError("la conexión debía fallar")

    exc = asyncio.run(_conectar())
    assert isinstance(exc, ConnectionError | OSError)
    assert es_caida_base_datos(exc)


def test_psycopg_al_conectar_se_reconoce() -> None:
    with pytest.raises(psycopg.OperationalError) as info:
        psycopg.connect(DSN_CERRADO, connect_timeout=2)
    assert es_caida_base_datos(info.value)


def test_operational_error_de_sqlalchemy_se_reconoce() -> None:
    error = sa_exc.OperationalError("SELECT 1", {}, Exception("server closed the connection"))
    assert es_caida_base_datos(error)


def test_interface_error_de_sqlalchemy_se_reconoce() -> None:
    error = sa_exc.InterfaceError("SELECT 1", {}, Exception("connection is closed"))
    assert es_caida_base_datos(error)


def test_la_causa_encadenada_cuenta() -> None:
    try:
        try:
            psycopg.connect(DSN_CERRADO, connect_timeout=2)
        except psycopg.OperationalError as causa:
            raise RuntimeError("la consulta falló") from causa
    except RuntimeError as exc:
        assert es_caida_base_datos(exc)


@pytest.mark.parametrize(
    "exc",
    [
        sa_exc.IntegrityError("INSERT", {}, Exception("duplicate key")),
        sa_exc.ProgrammingError("SELEC", {}, Exception("syntax error")),
        ValueError("entrada inválida"),
        ConnectionRefusedError("no viene de un driver de base de datos"),
        TimeoutError("tampoco"),
    ],
)
def test_lo_que_no_es_una_caida_de_la_base_no_se_reconoce(exc: BaseException) -> None:
    assert not es_caida_base_datos(exc)
    assert fallo_de_dependencia(exc) is None


def test_fallo_de_dependencia_devuelve_el_error_del_catalogo() -> None:
    error = fallo_de_dependencia(sa_exc.OperationalError("SELECT 1", {}, Exception("down")))
    assert isinstance(error, AppError)
    assert error.codigo == "DEPENDENCIA_NO_DISPONIBLE"
    assert error.http == 503
    assert error.detalle == {"servicio": SERVICIO_BASE_DATOS}


def test_fallo_de_dependencia_respeta_un_apperror_ya_tipificado() -> None:
    original = dependencia_no_disponible("embeddings")
    assert fallo_de_dependencia(original) is original


def test_otro_apperror_no_es_una_caida() -> None:
    assert fallo_de_dependencia(AppError("RECURSO_NO_ENCONTRADO", 404, "no")) is None


def test_una_cadena_ciclica_no_cuelga() -> None:
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__context__ = b
    b.__context__ = a
    assert not es_caida_base_datos(a)


def _dsn_pg() -> str | None:
    import os

    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1) if url else None


@pytest.mark.skipif(_dsn_pg() is None, reason="KC_DATABASE_URL_MIGRATIONS no definida")
def test_una_sentencia_que_supera_command_timeout_es_una_caida() -> None:
    """La cota del motor de la API (KC_DB_COMMAND_TIMEOUT_S): una conexión colgada termina en 503."""

    async def _colgada() -> BaseException:
        motor = create_async_engine(str(_dsn_pg()), connect_args={"command_timeout": 0.3})
        try:
            async with motor.connect() as conn:
                await conn.execute(text("SELECT pg_sleep(2)"))
        except Exception as exc:
            return exc
        finally:
            await motor.dispose()
        raise AssertionError("la sentencia debía agotar el tiempo")

    exc = asyncio.run(_colgada())
    assert isinstance(exc, TimeoutError)
    error = fallo_de_dependencia(exc)
    assert error is not None and error.codigo == "DEPENDENCIA_NO_DISPONIBLE"
