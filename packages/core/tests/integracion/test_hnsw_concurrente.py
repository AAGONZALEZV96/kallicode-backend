"""Construcción y reindexado concurrentes de HNSW como procedimiento operativo (KC-DB-08-2, DB-F10).

Comprueba la infraestructura de la migración 044 (catálogo, métricas, estado, pg_cron) y el script
db/06_indices_concurrentes.sql ejecutado con psql: que construye con CONCURRENTLY, que es idempotente,
que exige maintenance_work_mem >= 2 GB y, sobre todo, que una construcción concurrente INTERRUMPIDA deja un
índice inválido que se DETECTA (vec.v_hnsw_estado) y se RECONSTRUYE al volver a ejecutar el script, tanto en
una tabla normal como en una particionada (donde CONCURRENTLY no existe sobre el padre).

La carga concurrente sobre 10^6 filas (criterio 1 del ticket) no es una prueba automática: tarda horas.
Se ensayó a mano y está registrada en docs/runbook-vectorial.md.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
SCRIPT = RAIZ / "db" / "06_indices_concurrentes.sql"
MIGRACION = RAIZ / "db" / "migrations" / "044_hnsw_operaciones.sql"
REAL = ("ticket_embeddings_hnsw_ix", "doc_chunks_hnsw_ix", "definition_embeddings_hnsw_ix")
DIM = 8  # los índices de prueba no necesitan 1024 dimensiones


def _dsn(variable: str) -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _psql() -> str:
    for candidato in (
        shutil.which("psql"),
        r"C:\Program Files\PostgreSQL\17\bin\psql.exe",
        r"C:\Program Files\PostgreSQL\16\bin\psql.exe",
    ):
        if candidato and Path(candidato).exists():
            return candidato
    pytest.skip("psql no está instalado")


def _conexion(
    variable: str = "KC_DATABASE_URL_ADMIN", *, autocommit: bool = True
) -> psycopg.Connection:
    try:
        return psycopg.connect(_dsn(variable), autocommit=autocommit)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    c = _conexion()
    c.execute("SELECT '[1]'::vector")  # carga pgvector
    try:
        yield c
    finally:
        c.close()


def _ejecutar_script(*variables: str) -> subprocess.CompletedProcess[str]:
    cmd = [_psql(), _dsn("KC_DATABASE_URL_ADMIN"), "-v", "ON_ERROR_STOP=1", "-X"]
    for v in variables:
        cmd += ["-v", v]
    cmd += ["-f", str(SCRIPT)]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )


def _estado(admin: psycopg.Connection, indice: str) -> dict:
    cols = ["existe", "valido", "particiones_sin_indice", "indices_invalidos"]
    fila = admin.execute(
        "SELECT existe, valido, particiones_sin_indice, indices_invalidos FROM vec.v_hnsw_estado WHERE indice = %s",
        (indice,),
    ).fetchone()
    assert fila is not None, f"{indice} no está en el catálogo"
    return dict(zip(cols, fila, strict=True))


# --- el catálogo y el estado --------------------------------------------------------------------------------
def test_el_catalogo_coincide_con_los_indices_reales(admin: psycopg.Connection) -> None:
    filas = admin.execute(
        "SELECT indice, tabla, m, ef_construction FROM vec.hnsw_catalogo ORDER BY indice"
    ).fetchall()
    assert {f[0] for f in filas} >= set(REAL)
    for indice, _tabla, m, ef in filas:
        if indice not in REAL:
            continue
        opciones = admin.execute(
            "SELECT reloptions FROM pg_class WHERE oid = to_regclass(%s)", (f"vec.{indice}",)
        ).fetchone()[0]
        assert (
            opciones is not None and f"m={m}" in opciones and f"ef_construction={ef}" in opciones
        ), f"{indice}: el catálogo dice m={m}, ef_construction={ef} y el índice tiene {opciones}"


def test_los_tres_hnsw_estan_validos_y_completos(admin: psycopg.Connection) -> None:
    for indice in REAL:
        e = _estado(admin, indice)
        assert e == {
            "existe": True,
            "valido": True,
            "particiones_sin_indice": 0,
            "indices_invalidos": 0,
        }, (indice, e)


def test_el_rol_de_mantenimiento_trae_maintenance_work_mem_de_2gb(
    admin: psycopg.Connection,
) -> None:
    assert admin.execute(
        "SELECT pg_size_bytes(current_setting('maintenance_work_mem')) >= pg_size_bytes('2GB')"
    ).fetchone()[0], (
        "falta ALTER ROLE kallicode_admin SET maintenance_work_mem = '2GB' (db/00_roles.sql, como superusuario)"
    )


def test_la_aplicacion_no_ve_el_catalogo_ni_las_metricas() -> None:
    app = _conexion("KC_DATABASE_URL", autocommit=False)
    try:
        for tabla in ("vec.hnsw_catalogo", "vec.hnsw_metricas", "vec.v_hnsw_estado"):
            app.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(f"SELECT 1 FROM {tabla}")
            app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        app.close()


# --- las métricas ----------------------------------------------------------------------------------------------
def test_medir_registra_tamano_filas_hinchazon_y_maintenance_work_mem() -> None:
    c = _conexion(autocommit=False)
    try:
        c.execute("SELECT '[1]'::vector")
        filas = c.execute(
            "SELECT * FROM vec.medir_hnsw('construccion', 12.5, 'ticket_embeddings_hnsw_ix')"
        ).fetchall()
        assert len(filas) == 1
        asignadas = c.execute(
            "SELECT evento, duracion_s, maintenance_work_mem, particiones FROM vec.hnsw_metricas "
            "WHERE indice = 'ticket_embeddings_hnsw_ix' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert asignadas[0] == "construccion" and float(asignadas[1]) == 12.5
        assert asignadas[2] == "2GB" and asignadas[3] == 1
    finally:
        c.rollback()
        c.close()


def test_la_hinchazon_se_mide_contra_el_mejor_valor_registrado() -> None:
    c = _conexion(autocommit=False)
    try:
        c.execute("SELECT '[1]'::vector")
        c.execute(
            "INSERT INTO vec.hnsw_catalogo (indice, tabla) VALUES ('hnsw_hinchazon_ix', 'vec.hnsw_hinchazon') "
            "ON CONFLICT DO NOTHING"
        )
        c.execute("CREATE TABLE vec.hnsw_hinchazon (id int PRIMARY KEY, embedding vector(8))")
        c.execute(
            "INSERT INTO vec.hnsw_hinchazon SELECT g, ('[' || g || ',1,2,3,4,5,6,7]')::vector FROM generate_series(1, 300) g"
        )
        c.execute(
            "CREATE INDEX hnsw_hinchazon_ix ON vec.hnsw_hinchazon USING hnsw (embedding vector_cosine_ops)"
        )
        c.execute("ANALYZE vec.hnsw_hinchazon")
        c.execute("SELECT * FROM vec.medir_hnsw('construccion', 1, 'hnsw_hinchazon_ix')")
        base = c.execute(
            "SELECT bytes_por_fila, hinchazon_pct FROM vec.hnsw_metricas WHERE indice = 'hnsw_hinchazon_ix'"
        ).fetchone()
        assert base[0] is not None and float(base[1] or 0) == 0.0, (
            "la primera medición no tiene hinchazón"
        )
        # Los UPDATE dejan tuplas muertas sin compactar el grafo = más bytes de índice por fila viva.
        for _ in range(3):  # cada UPDATE añade una tupla nueva al grafo y deja la anterior
            c.execute("UPDATE vec.hnsw_hinchazon SET embedding = embedding")
        c.execute("ANALYZE vec.hnsw_hinchazon")
        c.execute("SELECT * FROM vec.medir_hnsw('antes', NULL, 'hnsw_hinchazon_ix')")
        antes = c.execute(
            "SELECT hinchazon_pct, filas_vivas FROM vec.hnsw_metricas WHERE indice = 'hnsw_hinchazon_ix' "
            "AND evento = 'antes'"
        ).fetchone()
        assert antes[0] is not None and float(antes[0]) > 0, (
            f"el UPDATE debía dejar hinchazón: {antes}"
        )
    finally:
        c.rollback()
        c.close()


# --- el script: construcción, idempotencia y límites ----------------------------------------------------------
def test_el_script_no_usa_transacciones_ni_crea_indices_sin_concurrently() -> None:
    sql = SCRIPT.read_text(encoding="utf-8")
    codigo = "\n".join(linea for linea in sql.splitlines() if not linea.lstrip().startswith("--"))
    assert not re.search(
        r"^\s*(BEGIN|COMMIT|START TRANSACTION)\b", codigo, re.MULTILINE | re.IGNORECASE
    )
    for crea in re.findall(r"format\('(CREATE INDEX[^']*)'", codigo):
        assert "CONCURRENTLY" in crea or "ON ONLY" in crea, f"índice sin CONCURRENTLY: {crea}"
    assert "ATTACH PARTITION" in codigo, "las tablas particionadas necesitan ON ONLY + ATTACH"


def test_el_script_es_idempotente_si_todo_esta_construido() -> None:
    r = _ejecutar_script()
    assert r.returncode == 0, r.stderr
    assert re.search(r"Índices construidos en esta ejecución:\s*0", r.stdout), r.stdout


def test_el_script_se_niega_con_menos_de_2gb_de_maintenance_work_mem() -> None:
    r = _ejecutar_script("mwm=512MB")
    assert r.returncode != 0
    assert "2 GB" in (r.stdout + r.stderr)
    # ...salvo en pruebas, explícitamente.
    assert _ejecutar_script("mwm=512MB", "permitir_poco_mwm=1", "solo=no_existe").returncode == 0


# --- criterio 3: una construcción concurrente interrumpida deja un índice inválido que se detecta y se reconstruye
@pytest.fixture(params=["tabla_normal", "tabla_particionada"])
def tabla_de_prueba(request: pytest.FixtureRequest, admin: psycopg.Connection) -> Iterator[dict]:
    particionada = request.param == "tabla_particionada"
    nombre = "hnsw_interrumpida"
    indice = f"{nombre}_hnsw_ix"
    admin.execute(f"DROP TABLE IF EXISTS vec.{nombre} CASCADE")
    admin.execute("DELETE FROM vec.hnsw_catalogo WHERE indice = %s", (indice,))
    if particionada:
        admin.execute(
            f"CREATE TABLE vec.{nombre} (id int NOT NULL, tenant text NOT NULL, embedding vector({DIM}), "
            "PRIMARY KEY (tenant, id)) PARTITION BY LIST (tenant)"
        )
        admin.execute(f"CREATE TABLE vec.{nombre}_a PARTITION OF vec.{nombre} FOR VALUES IN ('a')")
        destino = f"vec.{nombre}_a"
    else:
        admin.execute(f"CREATE TABLE vec.{nombre} (id int PRIMARY KEY, embedding vector({DIM}))")
        destino = f"vec.{nombre}"
    cols = "id, tenant, embedding" if particionada else "id, embedding"
    relleno = "g, 'a', " if particionada else "g, "
    admin.execute(
        f"INSERT INTO vec.{nombre} ({cols}) SELECT {relleno}array_fill(g::float4, ARRAY[{DIM}])::vector "
        "FROM generate_series(1, 2000) g"
    )
    admin.execute(
        "INSERT INTO vec.hnsw_catalogo (indice, tabla, m, ef_construction) VALUES (%s, %s, 8, 32)",
        (indice, f"vec.{nombre}"),
    )
    try:
        yield {
            "indice": indice,
            "tabla": f"vec.{nombre}",
            "destino": destino,
            "particionada": particionada,
        }
    finally:
        admin.execute(f"DROP TABLE IF EXISTS vec.{nombre} CASCADE")
        admin.execute("DELETE FROM vec.hnsw_catalogo WHERE indice = %s", (indice,))
        admin.execute("DELETE FROM vec.hnsw_metricas WHERE indice = %s", (indice,))


def _interrumpir_una_construccion(admin: psycopg.Connection, t: dict) -> None:
    """Lanza CREATE INDEX CONCURRENTLY, lo deja esperando a un escritor abierto y lo cancela a mitad."""
    if t["particionada"]:
        admin.execute(  # el índice del padre, como hace el script (ON ONLY: no admite CONCURRENTLY)
            f"CREATE INDEX {t['indice']} ON ONLY {t['tabla']} USING hnsw (embedding vector_cosine_ops) WITH (m = 8, ef_construction = 32)"
        )
    nombre_hijo = f"{t['indice']}_hijo" if t["particionada"] else t["indice"]
    escritor = _conexion(autocommit=False)
    resultado: list[Exception] = []

    def construir() -> None:
        c = _conexion(autocommit=True)
        try:
            c.execute(
                f"CREATE INDEX CONCURRENTLY {nombre_hijo} ON {t['destino']} USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 8, ef_construction = 32)"
            )
        except Exception as exc:
            resultado.append(exc)
        finally:
            c.close()

    try:
        # Una transacción de escritura abierta: la construcción concurrente la espera antes de seguir.
        escritor.execute(f"UPDATE {t['destino']} SET embedding = embedding WHERE id = 1")
        hilo = threading.Thread(target=construir)
        hilo.start()
        limite = time.time() + 30
        while time.time() < limite:
            fila = admin.execute(
                "SELECT pid FROM pg_stat_activity WHERE query LIKE 'CREATE INDEX CONCURRENTLY%%' "
                "AND state = 'active' AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'"
            ).fetchone()
            existe = admin.execute(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = %s",
                (nombre_hijo,),
            ).fetchone()
            if fila and existe is not None:
                admin.execute("SELECT pg_cancel_backend(%s)", (fila[0],))
                break
            time.sleep(0.1)
        else:
            pytest.fail("la construcción concurrente no llegó a esperar al escritor")
        hilo.join(timeout=30)
        assert resultado and isinstance(resultado[0], psycopg.errors.QueryCanceled), resultado
    finally:
        escritor.rollback()
        escritor.close()


def test_una_construccion_interrumpida_deja_un_indice_invalido_que_se_detecta_y_se_reconstruye(
    admin: psycopg.Connection, tabla_de_prueba: dict
) -> None:
    t = tabla_de_prueba
    nombre_hijo = f"{t['indice']}_hijo" if t["particionada"] else t["indice"]
    _interrumpir_una_construccion(admin, t)

    # 1. Queda un índice inválido (se mantiene en cada escritura pero ninguna consulta lo usa).
    invalido = admin.execute(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = %s",
        (nombre_hijo,),
    ).fetchone()
    assert invalido == (False,), "la interrupción debía dejar un índice inválido"

    # 2. Se detecta.
    e = _estado(admin, t["indice"])
    assert e["valido"] is False and e["indices_invalidos"] >= 1, e
    if t["particionada"]:
        assert e["particiones_sin_indice"] == 1, e

    # 3. Se reconstruye volviendo a ejecutar el script: retira el resto inválido y construye de nuevo.
    r = _ejecutar_script(f"solo={t['indice']}")
    assert r.returncode == 0, r.stdout + r.stderr
    e = _estado(admin, t["indice"])
    assert e == {
        "existe": True,
        "valido": True,
        "particiones_sin_indice": 0,
        "indices_invalidos": 0,
    }, e
    assert admin.execute(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE i.indrelid = ANY(SELECT relid FROM pg_partition_tree(%s::regclass)) AND NOT i.indisvalid",
        (t["tabla"],),
    ).fetchone() == (0,), "no debe quedar ningún índice inválido"
    # Registró la construcción (duración y maintenance_work_mem).
    metrica = admin.execute(
        "SELECT duracion_s, maintenance_work_mem FROM vec.hnsw_metricas WHERE indice = %s AND evento = 'construccion'",
        (t["indice"],),
    ).fetchone()
    assert metrica is not None and metrica[0] is not None and metrica[1] == "2GB"
    # El índice reconstruido sirve a las consultas.
    admin.execute("SET enable_seqscan = off")
    plan = "\n".join(
        f[0]
        for f in admin.execute(
            f"EXPLAIN SELECT id FROM {t['tabla']} ORDER BY embedding <=> array_fill(1::float4, ARRAY[{DIM}])::vector LIMIT 3"
        ).fetchall()
    )
    admin.execute("RESET enable_seqscan")
    assert "Index Scan" in plan, plan


def test_el_script_construye_el_indice_que_falta_sin_bloquear_escrituras(
    admin: psycopg.Connection, tabla_de_prueba: dict
) -> None:
    """Mientras el script construye, una escritura no queda esperando (CREATE INDEX CONCURRENTLY)."""
    t = tabla_de_prueba
    admin.execute(f"DROP INDEX IF EXISTS vec.{t['indice']}")
    espera = []

    def escribir() -> None:
        c = _conexion()
        try:
            for i in range(40):
                t0 = time.perf_counter()
                c.execute(
                    f"UPDATE {t['destino']} SET embedding = embedding WHERE id = %s", (1 + i,)
                )
                espera.append(time.perf_counter() - t0)
                time.sleep(0.02)
        finally:
            c.close()

    hilo = threading.Thread(target=escribir)
    hilo.start()
    r = _ejecutar_script(f"solo={t['indice']}")
    hilo.join()
    assert r.returncode == 0, r.stdout + r.stderr
    assert _estado(admin, t["indice"])["valido"] is True
    assert max(espera) < 5, f"una escritura esperó {max(espera):.1f} s durante la construcción"


# --- REINDEX CONCURRENTLY y pg_cron -------------------------------------------------------------------------------
def test_reindex_concurrently_funciona_sobre_los_tres_indices_incluso_los_particionados(
    admin: psycopg.Connection,
) -> None:
    for indice in REAL:
        admin.execute(f"REINDEX INDEX CONCURRENTLY vec.{indice}")
        assert _estado(admin, indice)["valido"] is True
        assert _estado(admin, indice)["indices_invalidos"] == 0


def test_los_trabajos_de_pg_cron_estan_programados(admin: psycopg.Connection) -> None:
    if admin.execute("SELECT to_regnamespace('cron') IS NULL").fetchone()[0]:
        pytest.skip("pg_cron no está instalada en este servidor (migración 044: solo avisa)")
    try:
        trabajos: dict[str, str] = dict(
            admin.execute(
                "SELECT jobname, command FROM cron.job WHERE jobname LIKE 'hnsw_%%'"
            ).fetchall()
        )
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("kallicode_admin no puede leer cron.job")
    esperados = {f"hnsw_reindex_{i}": f"REINDEX INDEX CONCURRENTLY vec.{i}" for i in REAL}
    for nombre, orden in esperados.items():
        assert trabajos.get(nombre) == orden, (
            f"falta o difiere el trabajo {nombre}: {trabajos.get(nombre)}"
        )
    assert "hnsw_medir_antes" in trabajos and "hnsw_medir_despues" in trabajos
    # Cada REINDEX es UNA sola sentencia: dentro de un bloque o una función no podría ser CONCURRENTLY.
    for orden in esperados.values():
        assert ";" not in orden


def test_el_ciclo_antes_reindex_despues_registra_hinchazon_y_duracion(
    admin: psycopg.Connection,
) -> None:
    admin.execute("DELETE FROM vec.hnsw_metricas WHERE indice = 'doc_chunks_hnsw_ix'")
    admin.execute("SELECT * FROM vec.medir_hnsw('antes', NULL, 'doc_chunks_hnsw_ix')")
    admin.execute("REINDEX INDEX CONCURRENTLY vec.doc_chunks_hnsw_ix")
    admin.execute("SELECT * FROM vec.medir_hnsw('despues', 0.5, 'doc_chunks_hnsw_ix')")
    filas = admin.execute(
        "SELECT evento, tamano_bytes, hinchazon_pct, duracion_s, maintenance_work_mem FROM vec.hnsw_metricas "
        "WHERE indice = 'doc_chunks_hnsw_ix' ORDER BY id"
    ).fetchall()
    assert [f[0] for f in filas] == ["antes", "despues"]
    assert all(f[1] is not None for f in filas) and filas[1][3] is not None
    assert all(f[4] == "2GB" for f in filas)
    admin.execute("DELETE FROM vec.hnsw_metricas WHERE indice = 'doc_chunks_hnsw_ix'")
