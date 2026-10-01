"""Runner de migraciones versionadas (D06, §8 del entorno · KC-DB-01-1).

Aplica los archivos inmutables db/migrations/NNN_descripcion.sql y los
registra en el ledger core.schema_migrations (definido en db/01_schema.sql).

Garantías:
  - Verificación previa: antes de aplicar nada se comprueba que cada archivo
    ya registrado sigue existiendo y conserva su sha256. Una edición
    retroactiva aborta la ejecución sin abrir ninguna transacción de datos.
  - Una transacción por archivo: la migración y su fila del ledger se
    confirman juntas; si falla la N, las anteriores quedan aplicadas.
  - pg_advisory_lock: dos runners concurrentes se serializan; el segundo
    espera y no reaplica lo que el primero ya registró.

Convenciones de los archivos:
  - Sin BEGIN/COMMIT propios: la transacción la abre el runner.
  - `-- kc:sin-transaccion` en una línea propia para sentencias que no
    admiten transacción (CREATE INDEX CONCURRENTLY). Se ejecutan sentencia
    a sentencia y deben ser reejecutables (IF NOT EXISTS).
  - El sha256 se calcula sobre el contenido con fin de línea LF, para que
    un checkout con CRLF (Windows) produzca el mismo hash que el CI.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from .logging import log

# Clave fija del advisory lock: los 8 primeros bytes del sha256 de un literal.
CLAVE_LOCK = int.from_bytes(
    hashlib.sha256(b"kallicode:core.schema_migrations").digest()[:8], "big", signed=True
)

# Debe coincidir con sm_archivo_chk de core.schema_migrations.
_NOMBRE_VALIDO = re.compile(r"^[0-9]{3}[a-z]?_[a-z0-9_]+\.sql$")
_MARCA_SIN_TRANSACCION = re.compile(r"^--\s*kc:sin-transaccion\s*$", re.MULTILINE)
# BEGIN de plpgsql no lleva ';' y END de plpgsql no se busca: no hay falsos positivos.
_TRANSACCION_EXPLICITA = re.compile(
    r"^\s*(BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;", re.IGNORECASE | re.MULTILINE
)
_DOLAR = re.compile(r"\$[A-Za-z_]*\$")


class MigracionError(Exception):
    """Error que detiene el runner; el mensaje nombra el archivo y la causa."""


@dataclass(frozen=True)
class Migracion:
    archivo: str
    sql: str
    sha256: str
    sin_transaccion: bool


@dataclass
class Resultado:
    aplicadas: list[str] = field(default_factory=list)
    adoptadas: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Parte 1 · Lectura y validación de los archivos
# ----------------------------------------------------------------------------
def normalizar(contenido: bytes) -> str:
    """Decodifica UTF-8 y unifica el fin de línea a LF."""
    return contenido.decode("utf-8").replace("\r\n", "\n")


def calcular_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def leer_migraciones(directorio: Path) -> list[Migracion]:
    """Lee y valida los archivos .sql del directorio, ordenados por nombre."""
    if not directorio.is_dir():
        raise MigracionError(f"No existe el directorio de migraciones: {directorio}")

    migraciones: list[Migracion] = []
    for ruta in sorted(directorio.glob("*.sql")):
        if not _NOMBRE_VALIDO.match(ruta.name):
            raise MigracionError(
                f"{ruta.name}: nombre inválido; se espera NNN_descripcion.sql "
                "en minúsculas (p. ej. 010_cm_indices.sql)"
            )
        sql = normalizar(ruta.read_bytes())
        if "\x00" in sql:
            raise MigracionError(f"{ruta.name}: contiene bytes NUL")
        if _TRANSACCION_EXPLICITA.search(sql):
            raise MigracionError(
                f"{ruta.name}: contiene BEGIN/COMMIT propios; quítalos, el runner "
                "envuelve cada archivo en su transacción (o usa -- kc:sin-transaccion)"
            )
        migraciones.append(
            Migracion(
                archivo=ruta.name,
                sql=sql,
                sha256=calcular_sha256(sql),
                sin_transaccion=bool(_MARCA_SIN_TRANSACCION.search(sql)),
            )
        )
    return migraciones


def dividir_sentencias(sql: str) -> list[str]:
    """Separa un script en sentencias por ';' respetando literales, comentarios
    y bloques $tag$...$tag$. Solo se usa en archivos sin transacción, donde
    cada sentencia debe enviarse por separado (CONCURRENTLY no admite
    scripts de varias sentencias)."""
    sentencias: list[str] = []
    actual: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            fin = sql.find("\n", i)
            fin = n if fin == -1 else fin
        elif sql.startswith("/*", i):
            fin = sql.find("*/", i + 2)
            fin = n if fin == -1 else fin + 2
        elif c in ("'", '"'):
            fin = i + 1
            while fin < n and not (sql[fin] == c and sql[fin + 1 : fin + 2] != c):
                fin += 2 if sql[fin] == c else 1  # comilla duplicada = escapada
            fin += 1
        elif c == "$" and (m := _DOLAR.match(sql, i)):
            cierre = sql.find(m.group(0), m.end())
            fin = n if cierre == -1 else cierre + len(m.group(0))
        elif c == ";":
            sentencias.append("".join(actual))
            actual = []
            i += 1
            continue
        else:
            fin = i + 1
        actual.append(sql[i:fin])
        i = fin
    sentencias.append("".join(actual))
    return [s.strip() for s in sentencias if _tiene_codigo(s)]


def _tiene_codigo(sentencia: str) -> bool:
    sin_comentarios = re.sub(r"--[^\n]*|/\*.*?\*/", "", sentencia, flags=re.DOTALL)
    return bool(sin_comentarios.strip())


def dsn_asyncpg(url: str) -> str:
    """Acepta la URL de SQLAlchemy (postgresql+asyncpg://) y la de libpq."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


# ----------------------------------------------------------------------------
# Parte 2 · Verificación del ledger
# ----------------------------------------------------------------------------
def _verificar_ledger(migraciones: list[Migracion], registradas: dict[str, str]) -> None:
    """Aborta si algún archivo ya aplicado cambió o desapareció."""
    por_archivo = {m.archivo: m for m in migraciones}
    problemas: list[str] = []
    for archivo, sha_registrado in sorted(registradas.items()):
        m = por_archivo.get(archivo)
        if m is None:
            problemas.append(f"{archivo}: aplicado en esta base pero ya no existe en el repo")
        elif m.sha256 != sha_registrado:
            problemas.append(
                f"{archivo}: el contenido cambió después de aplicarse "
                f"(ledger {sha_registrado[:12]}…, archivo {m.sha256[:12]}…)"
            )
    if problemas:
        raise MigracionError(
            "Edición retroactiva de migraciones; no se aplicó nada. Una migración "
            "aplicada es inmutable: crea un archivo NNN nuevo.\n  - " + "\n  - ".join(problemas)
        )


# ----------------------------------------------------------------------------
# Parte 3 · Aplicación de una migración
# ----------------------------------------------------------------------------
async def _registrar(conn: asyncpg.Connection, m: Migracion, duracion_ms: int) -> None:
    await conn.execute(
        "INSERT INTO core.schema_migrations (archivo, sha256, duracion_ms) VALUES ($1, $2, $3)",
        m.archivo,
        m.sha256,
        duracion_ms,
    )


async def _aplicar(conn: asyncpg.Connection, m: Migracion) -> int:
    t0 = time.perf_counter()
    try:
        if m.sin_transaccion:
            for sentencia in dividir_sentencias(m.sql):
                await conn.execute(sentencia)
            duracion = round((time.perf_counter() - t0) * 1000)
            await _registrar(conn, m, duracion)
        else:
            async with conn.transaction():
                await conn.execute(m.sql)
                duracion = round((time.perf_counter() - t0) * 1000)
                await _registrar(conn, m, duracion)
    except asyncpg.PostgresError as e:
        raise MigracionError(f"{m.archivo}: falló al aplicarse: {e}") from e
    return duracion


# ----------------------------------------------------------------------------
# Parte 4 · Punto de entrada
# ----------------------------------------------------------------------------
async def migrar(dsn: str, directorio: Path, *, adoptar: bool = False) -> Resultado:
    """Aplica las migraciones pendientes de `directorio` contra `dsn`.

    adoptar=True registra las pendientes en el ledger SIN ejecutarlas. Solo
    para entornos donde ya se aplicaron a mano antes de existir el runner.
    """
    migraciones = leer_migraciones(directorio)
    resultado = Resultado()

    conn = await asyncpg.connect(dsn_asyncpg(dsn))
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", CLAVE_LOCK)
        try:
            if await conn.fetchval("SELECT to_regclass('core.schema_migrations')") is None:
                raise MigracionError(
                    "No existe core.schema_migrations: aplica primero db/01..05.sql"
                )
            filas = await conn.fetch("SELECT archivo, sha256 FROM core.schema_migrations")
            registradas = {f["archivo"]: f["sha256"] for f in filas}
            _verificar_ledger(migraciones, registradas)

            ultima = max(registradas, default="")
            for m in migraciones:
                if m.archivo in registradas:
                    continue
                if m.archivo < ultima:
                    log.warning("migraciones.fuera_de_orden", archivo=m.archivo, ultima=ultima)
                if adoptar:
                    await _registrar(conn, m, 0)
                    resultado.adoptadas.append(m.archivo)
                    log.info("migraciones.adoptada", archivo=m.archivo)
                else:
                    duracion = await _aplicar(conn, m)
                    resultado.aplicadas.append(m.archivo)
                    log.info("migraciones.aplicada", archivo=m.archivo, duracion_ms=duracion)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", CLAVE_LOCK)
    finally:
        await conn.close()

    log.info(
        "migraciones.fin",
        aplicadas=len(resultado.aplicadas),
        adoptadas=len(resultado.adoptadas),
        total=len(migraciones),
    )
    return resultado
