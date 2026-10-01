"""Pruebas de integración del runner de migraciones (KC-DB-01-1, cierra DB-F02).

Cada prueba crea una base efímera con el DDL real de core.schema_migrations
(extraído de db/01_schema.sql) y un directorio de migraciones de juguete.
Requiere KC_DATABASE_URL_MIGRATIONS con un usuario que pueda crear bases.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from kallicode_core.migraciones import MigracionError, dsn_asyncpg, migrar

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRATE_PY = RAIZ / "packages" / "core" / "migrate.py"


def _ddl_ledger() -> str:
    esquema = (RAIZ / "db" / "01_schema.sql").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE core\.schema_migrations \(.*?\n\);", esquema, re.DOTALL)
    assert m, "core.schema_migrations no está en db/01_schema.sql"
    return m.group(0)


def _con_base(dsn: str, base: str) -> str:
    partes = urlsplit(dsn)
    return urlunsplit(partes._replace(path=f"/{base}"))


@pytest.fixture
async def base() -> AsyncIterator[str]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    dsn = dsn_asyncpg(url)
    nombre = f"kc_mig_{uuid.uuid4().hex[:12]}"

    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{nombre}"')
    dsn_temporal = _con_base(dsn, nombre)
    conn = await asyncpg.connect(dsn_temporal)
    await conn.execute("CREATE SCHEMA core")
    await conn.execute(_ddl_ledger())
    await conn.close()
    try:
        yield dsn_temporal
    finally:
        await admin.execute(f'DROP DATABASE "{nombre}" WITH (FORCE)')
        await admin.close()


def _escribir(directorio: Path, nombre: str, sql: str) -> None:
    (directorio / nombre).write_text(sql, encoding="utf-8")


async def _ledger(dsn: str) -> dict[str, asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        filas = await conn.fetch("SELECT * FROM core.schema_migrations ORDER BY archivo")
    finally:
        await conn.close()
    return {f["archivo"]: f for f in filas}


async def _existe(dsn: str, relacion: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", relacion)
    finally:
        await conn.close()


async def _ejecutar_cli(dsn: str, directorio: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(MIGRATE_PY),
        "--dsn",
        dsn,
        "--dir",
        str(directorio),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    salida, _ = await proc.communicate()
    return proc.returncode or 0, salida.decode("utf-8", errors="replace")


# --- Criterio 1: aplicar registra; reaplicar no hace nada -------------------
async def test_aplica_y_registra_sha256_y_duracion(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")
    _escribir(
        tmp_path, "002_b.sql", "CREATE TABLE public.b (id int);\nINSERT INTO public.b VALUES (1);"
    )

    resultado = await migrar(base, tmp_path)

    assert resultado.aplicadas == ["001_a.sql", "002_b.sql"]
    ledger = await _ledger(base)
    assert list(ledger) == ["001_a.sql", "002_b.sql"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in ledger.values())
    assert all(f["duracion_ms"] >= 0 for f in ledger.values())
    assert await _existe(base, "public.a")
    assert await _existe(base, "public.b")


async def test_reaplicar_no_hace_nada(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")
    await migrar(base, tmp_path)
    antes = await _ledger(base)

    resultado = await migrar(base, tmp_path)

    assert resultado.aplicadas == []
    assert await _ledger(base) == antes


# --- Criterio 2: edición retroactiva aborta sin aplicar nada ----------------
async def test_edicion_retroactiva_aborta_sin_aplicar_nada(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")
    await migrar(base, tmp_path)
    antes = await _ledger(base)

    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int, extra text);")
    _escribir(tmp_path, "002_b.sql", "CREATE TABLE public.b (id int);")

    with pytest.raises(MigracionError, match=r"001_a\.sql: el contenido cambió"):
        await migrar(base, tmp_path)

    assert not await _existe(base, "public.b"), "aplicó una pendiente pese a la edición"
    assert await _ledger(base) == antes


async def test_archivo_registrado_que_desaparece_aborta(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")
    await migrar(base, tmp_path)
    (tmp_path / "001_a.sql").unlink()

    with pytest.raises(
        MigracionError, match=r"001_a\.sql: aplicado en esta base pero ya no existe"
    ):
        await migrar(base, tmp_path)


# --- Transacción por archivo ------------------------------------------------
async def test_fallo_a_mitad_conserva_las_anteriores_y_no_deja_restos(
    base: str, tmp_path: Path
) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")
    _escribir(tmp_path, "002_b.sql", "CREATE TABLE public.b (id int);\nSELECT 1/0;")
    _escribir(tmp_path, "003_c.sql", "CREATE TABLE public.c (id int);")

    with pytest.raises(MigracionError, match=r"002_b\.sql: falló al aplicarse"):
        await migrar(base, tmp_path)

    assert list(await _ledger(base)) == ["001_a.sql"]
    assert await _existe(base, "public.a")
    assert not await _existe(base, "public.b"), "la 002 quedó aplicada a medias"
    assert not await _existe(base, "public.c"), "siguió aplicando tras el fallo"


async def test_sin_ledger_pide_aplicar_el_esquema_base(base: str, tmp_path: Path) -> None:
    conn = await asyncpg.connect(base)
    await conn.execute("DROP TABLE core.schema_migrations")
    await conn.close()
    _escribir(tmp_path, "001_a.sql", "SELECT 1;")

    with pytest.raises(MigracionError, match="aplica primero db/01..05.sql"):
        await migrar(base, tmp_path)


# --- Criterio 3: dos runners concurrentes no se pisan -----------------------
async def test_dos_procesos_concurrentes_no_se_pisan(base: str, tmp_path: Path) -> None:
    # Si ambos la aplicaran, el segundo CREATE TABLE fallaría con "already exists".
    _escribir(tmp_path, "001_lenta.sql", "CREATE TABLE public.lenta (id int);\nSELECT pg_sleep(2);")

    (rc1, out1), (rc2, out2) = await asyncio.gather(
        _ejecutar_cli(base, tmp_path), _ejecutar_cli(base, tmp_path)
    )

    assert (rc1, rc2) == (0, 0), f"\n--- runner 1:\n{out1}\n--- runner 2:\n{out2}"
    assert list(await _ledger(base)) == ["001_lenta.sql"]


# --- Criterio 4: el CLI falla con código distinto de cero -------------------
async def test_cli_devuelve_1_si_una_migracion_falla(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_mala.sql", "SELECT * FROM tabla_que_no_existe;")

    rc, salida = await _ejecutar_cli(base, tmp_path)

    assert rc == 1
    assert "001_mala.sql" in salida


# --- Convenciones adicionales ----------------------------------------------
async def test_sin_transaccion_permite_create_index_concurrently(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_t.sql", "CREATE TABLE public.t (x int);")
    _escribir(
        tmp_path,
        "002_ix.sql",
        "-- kc:sin-transaccion\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS t_x_ix ON public.t (x);\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS t_x2_ix ON public.t (x DESC);\n",
    )

    await migrar(base, tmp_path)

    assert await _existe(base, "public.t_x_ix")
    assert await _existe(base, "public.t_x2_ix")
    assert list(await _ledger(base)) == ["001_t.sql", "002_ix.sql"]


async def test_adoptar_registra_sin_ejecutar(base: str, tmp_path: Path) -> None:
    _escribir(tmp_path, "001_a.sql", "CREATE TABLE public.a (id int);")

    resultado = await migrar(base, tmp_path, adoptar=True)

    assert resultado.adoptadas == ["001_a.sql"]
    assert "001_a.sql" in await _ledger(base)
    assert not await _existe(base, "public.a")
