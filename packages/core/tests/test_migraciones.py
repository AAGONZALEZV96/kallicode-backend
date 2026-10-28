"""Pruebas unitarias del runner de migraciones (KC-DB-01-1): sin base de datos."""

from pathlib import Path

import pytest
from kallicode_core.migraciones import (
    MigracionError,
    calcular_sha256,
    dividir_sentencias,
    dsn_asyncpg,
    leer_migraciones,
    normalizar,
)


def _escribir(directorio: Path, nombre: str, sql: str) -> None:
    (directorio / nombre).write_bytes(sql.encode("utf-8"))


def test_crlf_y_lf_producen_el_mismo_hash() -> None:
    lf = normalizar(b"CREATE TABLE a (id int);\nSELECT 1;\n")
    crlf = normalizar(b"CREATE TABLE a (id int);\r\nSELECT 1;\r\n")
    assert calcular_sha256(lf) == calcular_sha256(crlf)


def test_lee_en_orden_y_calcula_hash(tmp_path: Path) -> None:
    _escribir(tmp_path, "010_b.sql", "SELECT 2;")
    _escribir(tmp_path, "002_a.sql", "SELECT 1;")
    _escribir(tmp_path, "004b_c.sql", "SELECT 3;")
    archivos = [m.archivo for m in leer_migraciones(tmp_path)]
    assert archivos == ["002_a.sql", "004b_c.sql", "010_b.sql"]


@pytest.mark.parametrize("nombre", ["1_algo.sql", "003_Mayus.sql", "003-guion.sql", "abc.sql"])
def test_nombre_invalido_se_rechaza(tmp_path: Path, nombre: str) -> None:
    _escribir(tmp_path, nombre, "SELECT 1;")
    with pytest.raises(MigracionError, match="nombre inválido"):
        leer_migraciones(tmp_path)


@pytest.mark.parametrize("sql", ["BEGIN;\nSELECT 1;\nCOMMIT;", "SELECT 1;\ncommit ;"])
def test_transaccion_explicita_se_rechaza(tmp_path: Path, sql: str) -> None:
    _escribir(tmp_path, "001_x.sql", sql)
    with pytest.raises(MigracionError, match="BEGIN/COMMIT"):
        leer_migraciones(tmp_path)


def test_bloque_plpgsql_no_es_transaccion_explicita(tmp_path: Path) -> None:
    _escribir(tmp_path, "001_x.sql", "DO $$\nBEGIN\n  PERFORM 1;\nEND;\n$$;\n")
    assert leer_migraciones(tmp_path)[0].archivo == "001_x.sql"


def test_bytes_nul_se_rechazan(tmp_path: Path) -> None:
    _escribir(tmp_path, "001_x.sql", "SELECT 1;\x00\x00")
    with pytest.raises(MigracionError, match="NUL"):
        leer_migraciones(tmp_path)


def test_directorio_inexistente(tmp_path: Path) -> None:
    with pytest.raises(MigracionError, match="No existe el directorio"):
        leer_migraciones(tmp_path / "no_existe")


def test_marca_sin_transaccion(tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "SELECT 1;")
    _escribir(tmp_path, "002_b.sql", "-- kc:sin-transaccion\nCREATE INDEX CONCURRENTLY x ON t (c);")
    a, b = leer_migraciones(tmp_path)
    assert not a.sin_transaccion
    assert b.sin_transaccion


def test_dividir_respeta_literales_comentarios_y_dolar() -> None:
    sql = """
    -- comentario con ; dentro
    INSERT INTO t VALUES ('a;b', 'it''s;');
    /* bloque ; */ SELECT "col;umna" FROM t;
    CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $fn$
    BEGIN RETURN 1; END;
    $fn$;
    """
    sentencias = dividir_sentencias(sql)
    assert len(sentencias) == 3
    assert "'a;b', 'it''s;'" in sentencias[0]
    assert '"col;umna"' in sentencias[1]
    assert "RETURN 1; END;" in sentencias[2]


def test_dividir_descarta_sentencias_vacias_y_solo_comentarios() -> None:
    assert dividir_sentencias("-- solo comentario\n;\n;SELECT 1;\n-- fin") == ["SELECT 1"]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("postgresql+asyncpg://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
    ],
)
def test_dsn_asyncpg(entrada: str, esperado: str) -> None:
    assert dsn_asyncpg(entrada) == esperado
