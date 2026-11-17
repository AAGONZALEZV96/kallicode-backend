#!/usr/bin/env python3
"""Ensayo de los seis incidentes de base de datos en el entorno de demostración (KC-DB-11-5).

Levanta un entorno desechable (PostgreSQL con la imagen de db/postgres + PgBouncer en modo transacción), carga el
esquema completo y la semilla de demostración (db/seeds/dev.sql) y, para CADA incidente del runbook
(docs/runbook_incidentes_db.md), hace lo que haría quien está de guardia:

    provocar el incidente  ->  detectarlo con la alerta  ->  diagnosticar  ->  contener  ->  resolver  ->  verificar

registrando el comando, el resultado y el tiempo de cada paso, y comprobando que la alerta (core.alertas_db) está activa
mientras dura el incidente y se apaga al resolverlo. El resultado es el acta del ensayo (Markdown y JSON).

    I1  Bloqueo prolongado por una migración         alerta DB-A01
    I2  Agotamiento de conexiones con PgBouncer      alerta DB-A02
    I3  Cadena de auditoría rota (PROVOCADA)         alerta DB-A03
    I4  Chequeo de integridad con hallazgos          alerta DB-A04
    I5  Hinchazón de un índice HNSW                  alerta DB-A05
    I6  Partición DEFAULT con filas                  alerta DB-A06

Qué NO es: no ensaya ningún canal de notificación (no hay Alertmanager en el repositorio: la «alerta» es la evaluación
de core.alertas_db, no un aviso a una persona), el PgBouncer del ensayo no es el de producción (no está en el repositorio:
se configura con valores de demostración) y los tiempos son los de un Docker local con datos mínimos.

Uso:  python db/tools/ensayo_incidentes.py --salida ./acta_incidentes [--solo I1 --solo I3] [--conservar] [--reusar]
Código de salida: 0 si todos los incidentes se ensayan y verifican, 1 si alguno falla.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
IMAGEN = "kallicode-postgres:16"
IMAGEN_BOUNCER = "edoburu/pgbouncer:latest"
BASE = "kallicode_test"
NOMBRES = {
    "red": "kc-inc-net",
    "pg": "kc-inc-pg",
    "bouncer": "kc-inc-bouncer",
}


def _cargar(nombre: str) -> ModuleType:
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


pitr = _cargar("ensayo_pitr")  # reutiliza la creación del esquema y la espera de PostgreSQL
worm = _cargar("worm_auditoria")


# ---------------------------------------------------------------------------------------------------------
# Registro del ensayo
# ---------------------------------------------------------------------------------------------------------
class Incidente:
    """Una fila del acta por cada paso: fase, qué se hizo (comando) y qué resultó, con el tiempo desde el inicio."""

    def __init__(self, ident: str, titulo: str, alerta: str) -> None:
        self.ident, self.titulo, self.alerta = ident, titulo, alerta
        self.pasos: list[dict[str, Any]] = []
        self.hitos: dict[str, float] = {}
        self.t0 = time.perf_counter()

    def registrar(self, fase: str, accion: str, ok: bool, resultado: Any = None) -> bool:
        self.pasos.append(
            {
                "fase": fase,
                "accion": accion,
                "ok": bool(ok),
                "resultado": resultado
                if isinstance(resultado, (int, float, bool)) or resultado is None
                else str(resultado),
                "t_s": round(time.perf_counter() - self.t0, 1),
            }
        )
        return bool(ok)

    def reiniciar(self) -> None:
        """Pone el reloj a cero al empezar el incidente: lo anterior es preparación y no cuenta."""
        for p in self.pasos:
            p["t_s"] = None
        self.t0 = time.perf_counter()

    def hito(self, nombre: str) -> None:
        self.hitos[nombre] = round(time.perf_counter() - self.t0, 1)

    @property
    def ok(self) -> bool:
        return bool(self.pasos) and all(p["ok"] for p in self.pasos)

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.ident,
            "titulo": self.titulo,
            "alerta": self.alerta,
            "ok": self.ok,
            "hitos_s": self.hitos,
            "pasos": self.pasos,
        }


# ---------------------------------------------------------------------------------------------------------
# Entorno
# ---------------------------------------------------------------------------------------------------------
class Entorno:
    def __init__(self) -> None:
        self.puerto_pg = 0
        self.puerto_bouncer = 0

    def dsn(
        self, usuario: str = "postgres", clave: str = "dev", base: str = BASE, bouncer: bool = False
    ) -> str:
        puerto = self.puerto_bouncer if bouncer else self.puerto_pg
        return f"postgresql://{usuario}:{clave}@127.0.0.1:{puerto}/{base}"

    def admin(self, autocommit: bool = True) -> Any:
        import psycopg

        c = psycopg.connect(self.dsn(), autocommit=autocommit)
        return c

    def alertas(self, **kw: Any) -> dict[str, dict[str, Any]]:
        """core.alertas_db() con umbrales de ensayo (más bajos que los de producción para no esperar minutos)."""
        p = {
            "p_umbral_bloqueo_s": 3,
            "p_umbral_conexiones": 0.8,
            "p_umbral_idle_tx_s": 2,
            "p_umbral_hinchazon": 30,
            "p_integridad": False,
        }
        p.update(kw)
        with self.admin() as c:
            filas = c.execute(
                "SELECT alerta, evaluada, activa, valor, umbral, detalle, runbook FROM core.alertas_db(%s::integer, %s::numeric, %s::integer, %s::numeric, %s::boolean)",
                (
                    p["p_umbral_bloqueo_s"],
                    p["p_umbral_conexiones"],
                    p["p_umbral_idle_tx_s"],
                    p["p_umbral_hinchazon"],
                    p["p_integridad"],
                ),
            ).fetchall()
        return {
            f[0]: {
                "evaluada": f[1],
                "activa": f[2],
                "valor": f[3],
                "umbral": f[4],
                "detalle": f[5],
                "runbook": f[6],
            }
            for f in filas
        }

    def bouncer_admin(self) -> Any:
        import psycopg

        return psycopg.connect(
            self.dsn("kallicode_app", "dev", "pgbouncer", bouncer=True), autocommit=True
        )


def _existe(nombre: str) -> bool:
    return (
        pitr._docker(
            "ps", "-a", "--filter", f"name=^{nombre}$", "--format", "{{.Names}}", check=False
        ).stdout.strip()
        == nombre
    )


def _levantar(reusar: bool) -> Entorno:
    env = Entorno()
    d = pitr._docker
    if reusar and _existe(NOMBRES["pg"]) and _existe(NOMBRES["bouncer"]):
        d("start", NOMBRES["pg"], check=False)
        d("start", NOMBRES["bouncer"], check=False)
        pitr._esperar_listo(NOMBRES["pg"])
    else:
        for c in (NOMBRES["bouncer"], NOMBRES["pg"]):
            d("rm", "-f", c, check=False)
        d("network", "rm", NOMBRES["red"], check=False)
        if d("image", "inspect", IMAGEN, check=False).returncode != 0:
            d("build", "-t", IMAGEN, str(RAIZ / "db" / "postgres"), timeout=1800)
        d("network", "create", NOMBRES["red"], check=False)
        d(
            "run", "-d", "--name", NOMBRES["pg"], "--network", NOMBRES["red"], "-e", "POSTGRES_PASSWORD=dev", "-e", f"POSTGRES_DB={BASE}",
            "-p", "127.0.0.1::5432", IMAGEN, "postgres",
            "-c", "max_connections=30", "-c", "log_lock_waits=on", "-c", "deadlock_timeout=1s",
            "-c", "shared_preload_libraries=pg_stat_statements,pg_partman_bgw,pg_cron",
            "-c", f"cron.database_name={BASE}", "-c", f"pg_partman_bgw.dbname={BASE}", "-c", "pg_partman_bgw.role=kallicode_admin",
        )  # fmt: skip
        pitr._esperar_listo(NOMBRES["pg"])
        env.puerto_pg = pitr._puerto(NOMBRES["pg"])
        ids = {"orig": NOMBRES["pg"]}
        pitr._esquema(ids, env.puerto_pg)
        semilla = (RAIZ / "db" / "seeds" / "dev.sql").read_text(encoding="utf-8")
        d(
            "exec",
            "-i",
            NOMBRES["pg"],
            "psql",
            "-U",
            "postgres",
            "-d",
            BASE,
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            entrada=semilla,
        )
        d(
            "run", "-d", "--name", NOMBRES["bouncer"], "--network", NOMBRES["red"], "-e", f"DB_HOST={NOMBRES['pg']}",
            "-e", "DB_USER=kallicode_app", "-e", "DB_PASSWORD=dev", "-e", f"DB_NAME={BASE}", "-e", "POOL_MODE=transaction",
            "-e", "DEFAULT_POOL_SIZE=4", "-e", "MIN_POOL_SIZE=0", "-e", "RESERVE_POOL_SIZE=0", "-e", "MAX_CLIENT_CONN=100",
            "-e", "AUTH_TYPE=scram-sha-256", "-e", "ADMIN_USERS=kallicode_app", "-e", "QUERY_WAIT_TIMEOUT=30",
            "-p", "127.0.0.1::5432", IMAGEN_BOUNCER,
        )  # fmt: skip
        time.sleep(3)
    env.puerto_pg = pitr._puerto(NOMBRES["pg"])
    env.puerto_bouncer = pitr._puerto(NOMBRES["bouncer"])
    return env


def _desmontar() -> None:
    for c in (NOMBRES["bouncer"], NOMBRES["pg"]):
        pitr._docker("rm", "-f", c, check=False)
    pitr._docker("network", "rm", NOMBRES["red"], check=False)


def _hilo(fn: Any) -> threading.Thread:
    h = threading.Thread(target=fn, daemon=True)
    h.start()
    return h


# ---------------------------------------------------------------------------------------------------------
# I1 · Bloqueo prolongado por una migración
# ---------------------------------------------------------------------------------------------------------
SQL_I1_ESPERAS = (
    "SELECT a.pid, a.state, round(extract(epoch FROM now() - a.state_change)) AS espera_s, pg_blocking_pids(a.pid) AS bloqueada_por, left(a.query, 60) "
    "FROM pg_stat_activity a WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' ORDER BY a.state_change"
)
SQL_I1_BLOQUEANTES = (
    "SELECT b.pid, b.usename, b.state, round(extract(epoch FROM now() - b.xact_start)) AS tx_abierta_s, left(b.query, 60) "
    "FROM pg_stat_activity b WHERE b.pid IN (SELECT unnest(pg_blocking_pids(a.pid)) FROM pg_stat_activity a WHERE a.wait_event_type = 'Lock')"
)


def incidente_i1(env: Entorno) -> Incidente:
    import psycopg

    inc = Incidente("I1", "Bloqueo prolongado por una migración", "DB-A01")
    res: dict[str, Any] = {}
    with env.admin() as limpio:
        limpio.execute("ALTER TABLE core.organizations DROP COLUMN IF EXISTS zz_incidente")

    # Provocación: una transacción olvidada (idle in transaction) retiene un bloqueo de lectura; una migración sin
    # lock_timeout pide ACCESS EXCLUSIVE y espera; y la aplicación, detrás de ella en la cola, también espera.
    holder = psycopg.connect(env.dsn(), autocommit=False)
    holder.execute("SELECT count(*) FROM core.organizations")
    pid_holder = holder.info.backend_pid

    def migracion() -> None:
        c = psycopg.connect(env.dsn(), autocommit=True)
        res["pid_migracion"] = c.info.backend_pid
        try:
            c.execute("ALTER TABLE core.organizations ADD COLUMN zz_incidente text")
            res["migracion"] = "terminó"
        except psycopg.errors.QueryCanceled:
            res["migracion"] = "cancelada"
        except Exception as exc:
            res["migracion"] = type(exc).__name__

    def aplicacion() -> None:
        c = psycopg.connect(env.dsn(), autocommit=True)
        t = time.perf_counter()
        c.execute("SELECT count(*) FROM core.organizations").fetchone()
        res["app_espera_s"] = round(time.perf_counter() - t, 1)

    h_mig = _hilo(migracion)
    time.sleep(1)
    h_app = _hilo(aplicacion)
    time.sleep(4.5)
    inc.registrar(
        "provocación",
        "transacción abierta + ALTER TABLE sin lock_timeout + consulta de la aplicación en cola",
        True,
        f"bloqueante pid {pid_holder}",
    )
    inc.hito("incidente_activo")

    # Detección
    a = env.alertas()["DB-A01"]
    inc.registrar(
        "detección",
        "SELECT * FROM core.alertas_db() -> DB-A01",
        a["activa"] is True,
        f"valor {a['valor']} s ≥ umbral {a['umbral']} s; {a['detalle']}",
    )
    inc.hito("detectado")

    # Diagnóstico
    with env.admin() as c:
        esperas = c.execute(SQL_I1_ESPERAS).fetchall()
        bloqueantes = c.execute(SQL_I1_BLOQUEANTES).fetchall()
    pid_mig = next((e[0] for e in esperas if str(e[4]).upper().startswith("ALTER")), None)
    raiz = [b for b in bloqueantes if b[0] == pid_holder]
    inc.registrar(
        "diagnóstico",
        "pg_stat_activity + pg_blocking_pids: quién espera y quién bloquea",
        pid_mig == res.get("pid_migracion") and len(esperas) >= 2,
        f"{len(esperas)} esperando; migración pid {pid_mig}",
    )
    inc.registrar(
        "diagnóstico",
        "bloqueante raíz: transacción abierta sin actividad",
        bool(raiz) and raiz[0][2] == "idle in transaction",
        raiz[0][2:4] if raiz else "no encontrado",
    )

    # Contención: cancelar la migración (no el bloqueante): la cola se libera y la aplicación sigue
    with env.admin() as c:
        c.execute("SELECT pg_cancel_backend(%s)", (pid_mig,))
    h_mig.join(timeout=10)
    h_app.join(timeout=10)
    inc.registrar(
        "contención",
        "pg_cancel_backend(<pid de la migración>)",
        res.get("migracion") == "cancelada" and "app_espera_s" in res,
        f"migración {res.get('migracion')}; la aplicación esperó {res.get('app_espera_s')} s",
    )
    inc.hito("contenido")

    # Resolución: la migración vuelve a lanzarse con lock_timeout (falla rápido sin encolar) y, tras liberar al bloqueante, termina
    c = psycopg.connect(env.dsn(), autocommit=True)
    c.execute("SET lock_timeout = '2s'")
    t = time.perf_counter()
    try:
        c.execute("ALTER TABLE core.organizations ADD COLUMN zz_incidente text")
        rapido = False
    except psycopg.errors.LockNotAvailable:
        rapido = time.perf_counter() - t < 4
    inc.registrar(
        "resolución",
        "reintento con SET lock_timeout = '2s' mientras el bloqueante sigue: falla rápido",
        rapido,
        "LockNotAvailable sin encolar",
    )
    with env.admin() as adm:
        terminado = adm.execute("SELECT pg_terminate_backend(%s)", (pid_holder,)).fetchone()
    if not holder.closed:
        holder.close()
    inc.registrar(
        "resolución",
        "pg_terminate_backend(<bloqueante>) tras confirmar que es una transacción olvidada",
        bool(terminado and terminado[0]),
        None,
    )
    try:
        c.execute("ALTER TABLE core.organizations ADD COLUMN zz_incidente text")
        migrado = True
    except Exception as exc:
        migrado = False
        res["error"] = str(exc)
    inc.registrar(
        "resolución", "reintento de la migración con lock_timeout", migrado, res.get("error")
    )
    inc.hito("resuelto")

    # Verificación
    with env.admin() as adm:
        quedan = adm.execute(SQL_I1_ESPERAS).fetchall()
        col = adm.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema='core' AND table_name='organizations' AND column_name='zz_incidente'"
        ).fetchone()
        adm.execute("ALTER TABLE core.organizations DROP COLUMN IF EXISTS zz_incidente")
    a = env.alertas()["DB-A01"]
    inc.registrar(
        "verificación",
        "sin sesiones en espera; migración aplicada una sola vez; DB-A01 apagada",
        not quedan and col is not None and col[0] == 1 and a["activa"] is False,
        f"valor {a['valor']}",
    )
    inc.hito("verificado")
    return inc


# ---------------------------------------------------------------------------------------------------------
# I2 · Agotamiento de conexiones con PgBouncer
# ---------------------------------------------------------------------------------------------------------
SQL_I2_SESIONES = (
    "SELECT usename, state, count(*) AS n, round(max(extract(epoch FROM now() - state_change))) AS la_mas_antigua_s "
    "FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1, 2 ORDER BY 3 DESC"
)
SQL_I2_IDLE_TX = (
    "SELECT pid, usename, round(extract(epoch FROM now() - xact_start)) AS tx_abierta_s, left(query, 50) "
    "FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY xact_start"
)


def _pools(env: Entorno) -> dict[str, Any]:
    with env.bouncer_admin() as b:
        cur = b.execute("SHOW POOLS")
        cols = [d.name for d in cur.description]
        for f in cur.fetchall():
            fila = dict(zip(cols, f, strict=True))
            if fila["database"] == BASE:
                return fila
    return {}


def incidente_i2(env: Entorno) -> Incidente:
    import psycopg

    inc = Incidente("I2", "Agotamiento de conexiones con PgBouncer", "DB-A02")
    res: dict[str, Any] = {}
    # Provocación: el pool es de 4 conexiones; cuatro clientes dejan una transacción abierta y la quinta consulta espera.
    titulares = []
    for _ in range(4):
        c = psycopg.connect(
            env.dsn("kallicode_app", "dev", bouncer=True), autocommit=False, prepare_threshold=None
        )
        c.execute("SELECT 1")
        titulares.append(c)

    def quinto() -> None:
        c = psycopg.connect(
            env.dsn("kallicode_app", "dev", bouncer=True), autocommit=True, prepare_threshold=None
        )
        t = time.perf_counter()
        try:
            c.execute("SELECT 1").fetchone()
            res["quinto"] = "ok"
        except Exception as exc:
            res["quinto"] = type(exc).__name__
        res["quinto_espera_s"] = round(time.perf_counter() - t, 1)

    h = _hilo(quinto)
    time.sleep(3.5)
    p = _pools(env)
    inc.registrar(
        "provocación",
        "4 transacciones abiertas a través de PgBouncer (pool de 4) + una quinta consulta",
        True,
        f"cl_active {p.get('cl_active')}, cl_waiting {p.get('cl_waiting')}, sv_active {p.get('sv_active')}",
    )
    inc.hito("incidente_activo")

    # Detección: PgBouncer (cl_waiting) y la alerta de la base (transacciones colgadas)
    a = env.alertas()["DB-A02"]
    inc.registrar(
        "detección",
        "SHOW POOLS en PgBouncer: clientes esperando conexión",
        p.get("cl_waiting", 0) >= 1,
        f"cl_waiting {p.get('cl_waiting')}, maxwait {p.get('maxwait')} s",
    )
    inc.registrar(
        "detección",
        "SELECT * FROM core.alertas_db() -> DB-A02 (transacciones colgadas)",
        a["activa"] is True,
        a["detalle"],
    )
    inc.hito("detectado")

    # Diagnóstico
    with env.admin() as c:
        sesiones = c.execute(SQL_I2_SESIONES).fetchall()
        idle = c.execute(SQL_I2_IDLE_TX).fetchall()
    inc.registrar(
        "diagnóstico",
        "pg_stat_activity agrupado por rol y estado",
        any(
            s[0] == "kallicode_app" and s[1] == "idle in transaction" and s[2] == 4
            for s in sesiones
        ),
        [tuple(s) for s in sesiones],
    )
    inc.registrar(
        "diagnóstico",
        "transacciones abiertas sin actividad (las que retienen el pool)",
        len(idle) == 4,
        f"{len(idle)} sesiones idle in transaction",
    )

    # Contención: terminar las transacciones colgadas libera el pool y atiende a la cola
    with env.admin() as c:
        n = c.execute(
            "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' "
            "AND usename = 'kallicode_app' AND now() - state_change > interval '2 seconds') t"
        ).fetchone()
    h.join(timeout=15)
    inc.registrar(
        "contención",
        "pg_terminate_backend de las idle in transaction del rol de la aplicación con más de 2 s",
        bool(n and n[0] == 4) and res.get("quinto") == "ok",
        f"terminadas {n[0] if n else '?'}; la quinta consulta {res.get('quinto')} tras {res.get('quinto_espera_s')} s",
    )
    inc.hito("contenido")
    for c in titulares:
        if not c.closed:
            c.close()

    # Resolución: que una transacción olvidada no pueda retener el pool (idle_in_transaction_session_timeout del rol)
    with env.admin() as c:
        c.execute("ALTER ROLE kallicode_app SET idle_in_transaction_session_timeout = '3s'")
    with env.bouncer_admin() as b:
        b.execute(
            f"RECONNECT {BASE}"
        )  # las conexiones del pool ya abiertas no tienen el ajuste nuevo
    time.sleep(1)
    olvidada = psycopg.connect(
        env.dsn("kallicode_app", "dev", bouncer=True), autocommit=False, prepare_threshold=None
    )
    olvidada.execute("SELECT 1")
    time.sleep(5)
    try:
        olvidada.execute("SELECT 1")
        cortada = False
    except psycopg.Error:
        cortada = True
    with env.admin() as c:
        quedan = c.execute(SQL_I2_IDLE_TX).fetchall()
    inc.registrar(
        "resolución",
        "ALTER ROLE kallicode_app SET idle_in_transaction_session_timeout + RECONNECT: una transacción olvidada se corta sola",
        cortada and not quedan,
        f"cortada por el servidor tras ~3 s; idle in transaction restantes: {len(quedan)}",
    )
    with env.admin() as c:
        c.execute(
            "ALTER ROLE kallicode_app RESET idle_in_transaction_session_timeout"
        )  # el ensayo no deja el ajuste de demostración
    with env.bouncer_admin() as b:
        b.execute(f"RECONNECT {BASE}")
    inc.hito("resuelto")

    # Variante: agotamiento directo de max_connections (sin pasar por el pool)
    directas = []
    try:
        for _ in range(25):
            directas.append(psycopg.connect(env.dsn(), autocommit=True))
        a2 = env.alertas()["DB-A02"]
        inc.registrar(
            "detección",
            "variante: 25 conexiones directas con max_connections = 30 -> DB-A02 por ratio",
            a2["activa"] is True and a2["valor"] >= 0.8,
            a2["detalle"],
        )
    finally:
        for c in directas:
            c.close()
    time.sleep(1)

    # Verificación
    p = _pools(env)
    a = env.alertas()["DB-A02"]
    c = psycopg.connect(
        env.dsn("kallicode_app", "dev", bouncer=True), autocommit=True, prepare_threshold=None
    )
    sirve = c.execute("SELECT 1").fetchone() == (1,)
    c.close()
    inc.registrar(
        "verificación",
        "cl_waiting = 0, el pool sirve consultas y DB-A02 apagada",
        p.get("cl_waiting") == 0 and sirve and a["activa"] is False,
        f"cl_waiting {p.get('cl_waiting')}; {a['detalle']}",
    )
    inc.hito("verificado")
    return inc


# ---------------------------------------------------------------------------------------------------------
# I3 · Cadena de auditoría rota (provocada a propósito)
# ---------------------------------------------------------------------------------------------------------
def incidente_i3(env: Entorno) -> Incidente:
    import psycopg

    inc = Incidente("I3", "Cadena de auditoría rota (provocada a propósito)", "DB-A03")
    tenant = "org_demo"
    with tempfile.TemporaryDirectory() as tmp, psycopg.connect(env.dsn(), autocommit=False) as conn:
        # Preparación: eventos reales (por el disparador de sellado) y un export WORM verificado de su partición
        for i in range(8):
            conn.execute(
                "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
                "VALUES (%s, 'sistema', 'ensayo_i3', %s, %s, %s, '10.0.0.7', 'ensayo/1', %s)",
                (tenant, f"evento {i}", "0" * 64, "a" * 64, f"req-i3-{uuid.uuid4().hex[:8]}"),
            )
        conn.commit()
        particion = conn.execute(
            "SELECT DISTINCT tableoid::regclass::text FROM audit.audit_events WHERE evento = 'ensayo_i3'"
        ).fetchone()
        assert particion is not None
        exp = worm.exportar(conn, particion[0], Path(tmp), "ensayo_incidentes")
        conn.commit()
        sha_man = conn.execute(
            "SELECT manifiesto_sha256 FROM audit.worm_exports WHERE id = %s", (exp["export_id"],)
        ).fetchone()[0]
        ok0 = conn.execute("SELECT integra FROM audit.verify_chain(%s)", (tenant,)).fetchone()
        inc.registrar(
            "preparación",
            "export WORM verificado de la partición antes del incidente",
            bool(ok0 and ok0[0]),
            f"{exp['filas']} eventos; manifiesto {sha_man[:12]}…",
        )
        inc.reiniciar()

        # Provocación: alteración de un evento saltándose el disparador de inmutabilidad (solo un superusuario puede)
        objetivo = conn.execute(
            "SELECT id FROM audit.audit_events WHERE evento = 'ensayo_i3' ORDER BY id OFFSET 4 LIMIT 1"
        ).fetchone()[0]
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE audit.audit_events SET resumen = 'ALTERADO' WHERE id = %s", (objetivo,)
        )
        conn.execute("SET LOCAL session_replication_role = origin")
        conn.commit()
        inc.registrar(
            "provocación",
            "UPDATE de un evento con los disparadores desactivados (manipulación)",
            True,
            f"evento {objetivo}",
        )
        inc.hito("incidente_activo")

        # Detección
        a = env.alertas(p_integridad=True)["DB-A03"]
        inc.registrar(
            "detección",
            "SELECT * FROM core.alertas_db(p_integridad => true) -> DB-A03",
            a["activa"] is True,
            a["detalle"],
        )
        inc.hito("detectado")

        # Diagnóstico
        v = conn.execute(
            "SELECT integra, primer_evento_roto, eventos_verificados FROM audit.verify_chain(%s)",
            (tenant,),
        ).fetchone()
        inc.registrar(
            "diagnóstico",
            "audit.verify_chain(<tenant>): integra = false y primer evento roto",
            v is not None and v[0] is False and v[1] == objetivo,
            f"primer evento roto {v[1] if v else None}",
        )
        dif = worm.comparar_con_export(conn, Path(exp["manifiesto"]))
        inc.registrar(
            "diagnóstico",
            "worm_auditoria.py comparar: qué evento y qué columnas difieren de su export WORM",
            [d["id"] for d in dif["alterados"]] == [objetivo]
            and dif["alterados"][0]["columnas"] == ["resumen"]
            and not dif["borrados"],
            f"alterado el evento {objetivo}, columnas {dif['alterados'][0]['columnas'] if dif['alterados'] else None}; borrados {dif['borrados']}",
        )
        digest_export = conn.execute(
            "SELECT contenido_digest FROM audit.worm_exports WHERE id = %s", (exp["export_id"],)
        ).fetchone()[0]
        problemas = worm.verificar_archivo(Path(exp["manifiesto"]), None, sha_man)
        inc.registrar(
            "diagnóstico",
            "el export WORM sigue íntegro (verificación externa con el hash registrado)",
            not problemas,
            "export intacto" if not problemas else problemas,
        )

        # Contención: preservar evidencia y no hacer nada destructivo (la política de la auditoría ya es «conservar»)
        evidencia = conn.execute(
            "SELECT count(*) FROM (SELECT * FROM audit.audit_events WHERE tenant_id = %s) t",
            (tenant,),
        ).fetchone()[0]
        copia = Path(tmp) / "evidencia_estado_alterado.csv"
        with (
            conn.cursor() as cur,
            copia.open("wb") as fh,
            cur.copy(
                "COPY (SELECT * FROM audit.audit_events WHERE tenant_id = '"
                + tenant
                + "' ORDER BY id) TO STDOUT WITH CSV HEADER"
            ) as cp,
        ):
            for datos in cp:
                fh.write(bytes(datos))
        politica = conn.execute(
            "SELECT action FROM core.retention_policies WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL"
        ).fetchone()
        rechaza = False
        try:
            worm.exportar(conn, particion[0], Path(tmp) / "no", "ensayo")
        except ValueError:
            rechaza = True
        conn.rollback()
        inc.registrar(
            "contención",
            "COPY de la evidencia (estado alterado) a un archivo y comprobación de que no se purga ni se exporta la partición rota",
            copia.stat().st_size > 0 and politica == ("conservar",) and rechaza,
            f"{evidencia} eventos copiados; política {politica[0]}; exportar rechaza la cadena rota",
        )
        inc.hito("contenido")

        # Resolución: devolver el evento a su contenido original desde el export verificado
        res = worm.restaurar_evento(conn, Path(exp["manifiesto"]), objetivo, sha_man)
        conn.commit()
        inc.registrar(
            "resolución",
            "worm_auditoria.py restaurar --evento <id> --sha256-manifiesto <registrado>",
            res["evento"] == objetivo,
            f"columnas restauradas: {len(res['columnas_restauradas'])}",
        )
        inc.hito("resuelto")

        # Verificación
        v = conn.execute(
            "SELECT integra, eventos_verificados, eventos_esperados FROM audit.verify_chain(%s)",
            (tenant,),
        ).fetchone()
        digest_final = conn.execute(
            "SELECT audit.digest_particion(%s::regclass)", (particion[0],)
        ).fetchone()[0]
        a = env.alertas(p_integridad=True)["DB-A03"]
        inc.registrar(
            "verificación",
            "verify_chain íntegra, partición idéntica al export y DB-A03 apagada",
            bool(v and v[0]) and digest_final == digest_export and a["activa"] is False,
            f"{v[1] if v else '?'} eventos verificados; {a['detalle']}",
        )
        inc.hito("verificado")
        # El ensayo no deja los eventos de prueba alterados: están restaurados, y son append-only (se dejan como evidencia)
    return inc


# ---------------------------------------------------------------------------------------------------------
# I4 · Chequeo de integridad con hallazgos
# ---------------------------------------------------------------------------------------------------------
SQL_I4_HALLAZGOS = "SELECT chequeo, descripcion, problemas FROM core.validar_integridad() WHERE problemas > 0 ORDER BY chequeo"
SQL_I4_V01 = (
    "SELECT o.id FROM core.organizations o LEFT JOIN core.subscriptions s ON s.tenant_id = o.id AND s.finaliza_el IS NULL "
    "WHERE o.eliminado_en IS NULL GROUP BY o.id HAVING count(s.id) <> 1"
)


def incidente_i4(env: Entorno) -> Incidente:
    inc = Incidente("I4", "Chequeo de integridad con hallazgos", "DB-A04")
    with env.admin() as c:
        c.execute("DELETE FROM core.organizations WHERE id = 'org_sin_suscripcion'")
        base = c.execute(
            "SELECT coalesce(sum(problemas), 0) FROM core.validar_integridad()"
        ).fetchone()[0]
    inc.registrar(
        "preparación",
        "línea base: core.validar_integridad() sin hallazgos",
        base == 0,
        f"{base} hallazgos",
    )
    inc.reiniciar()

    with env.admin() as c:
        c.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES ('org_sin_suscripcion', 'Sin suscripción')"
        )
    inc.registrar(
        "provocación",
        "organización creada sin suscripción vigente (V01)",
        True,
        "org_sin_suscripcion",
    )
    inc.hito("incidente_activo")

    a = env.alertas(p_integridad=True)["DB-A04"]
    inc.registrar(
        "detección",
        "SELECT * FROM core.alertas_db(p_integridad => true) -> DB-A04",
        a["activa"] is True,
        a["detalle"],
    )
    inc.hito("detectado")

    with env.admin() as c:
        hall = c.execute(SQL_I4_HALLAZGOS).fetchall()
        quien = c.execute(SQL_I4_V01).fetchall()
    inc.registrar(
        "diagnóstico",
        "core.validar_integridad() WHERE problemas > 0: qué chequeo y cuántos",
        [h[0] for h in hall] == ["V01_suscripcion_vigente"],
        [(h[0], h[2]) for h in hall],
    )
    inc.registrar(
        "diagnóstico",
        "consulta de detalle del chequeo (V01): qué tenants",
        quien == [("org_sin_suscripcion",)],
        quien,
    )

    inc.registrar(
        "contención",
        "no se corrige a ciegas: el hallazgo no se propaga (el tenant afectado queda identificado) y se decide la corrección con el dueño del dato",
        True,
        "decisión humana: dar de alta la suscripción",
    )
    inc.hito("contenido")

    with env.admin() as c:
        c.execute(
            "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el) VALUES ('sub_org_sin_suscripcion', 'org_sin_suscripcion', 'growth', current_date, current_date + 30)"
        )
    inc.registrar(
        "resolución",
        "alta de la suscripción del tenant afectado",
        True,
        "INSERT INTO core.subscriptions",
    )
    inc.hito("resuelto")

    with env.admin() as c:
        total = c.execute(
            "SELECT count(*), coalesce(sum(problemas), 0) FROM core.validar_integridad()"
        ).fetchone()
    a = env.alertas(p_integridad=True)["DB-A04"]
    inc.registrar(
        "verificación",
        "26 chequeos en 0 y DB-A04 apagada",
        total == (26, 0) and a["activa"] is False,
        f"{total[0]} chequeos, {total[1]} hallazgos",
    )
    inc.hito("verificado")
    with env.admin() as c:  # limpieza del ensayo
        c.execute("DELETE FROM core.subscriptions WHERE tenant_id = 'org_sin_suscripcion'")
        c.execute("DELETE FROM core.organizations WHERE id = 'org_sin_suscripcion'")
    return inc


# ---------------------------------------------------------------------------------------------------------
# I5 · Hinchazón de un índice HNSW
# ---------------------------------------------------------------------------------------------------------
def incidente_i5(env: Entorno) -> Incidente:
    import numpy as np
    import psycopg

    hnsw = _cargar("ensayo_hnsw_reconstruccion")
    inc = Incidente("I5", "Hinchazón de un índice HNSW", "DB-A05")
    indice = "doc_chunks_hnsw_ix"
    n = 1500
    with psycopg.connect(env.dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (hnsw.TENANT,))
        rng = np.random.default_rng(3)
        m = hnsw._unit(rng, n)
        with (
            c.cursor() as cur,
            cur.copy(
                "COPY vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding, texto_hash) FROM STDIN (FORMAT BINARY)"
            ) as cp,
        ):
            cp.set_types(["text", "text", "text", "int4", "text", "bytea", "text"])
            for i in range(n):
                cp.write_row(
                    (f"ch_{i}", hnsw.TENANT, "doc_i5", i, "contenido", hnsw._vec(m[i]), "h")
                )
        c.execute("VACUUM ANALYZE vec.doc_chunks")
        c.execute(
            "SELECT * FROM vec.medir_hnsw('despues', 0.1::numeric, %s::text)", (indice,)
        )  # línea base
        base = c.execute(
            "SELECT hinchazon_pct FROM vec.estado_hnsw() WHERE indice = %s", (indice,)
        ).fetchone()
    inc.registrar(
        "preparación",
        "índice cargado y línea base registrada con vec.medir_hnsw",
        base is not None and base[0] is not None and base[0] < 30,
        f"hinchazón de partida {base[0] if base else None} %",
    )
    inc.reiniciar()

    # Provocación: se borra el 75 % de las filas (el índice HNSW no devuelve el espacio con el VACUUM)
    with psycopg.connect(env.dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "DELETE FROM vec.doc_chunks WHERE tenant_id = %s AND chunk_index %% 4 <> 0",
            (hnsw.TENANT,),
        )
        c.execute("VACUUM ANALYZE vec.doc_chunks")
    inc.registrar(
        "provocación",
        "borrado del 75 % de las filas + VACUUM (la rotación diaria del refresco, en pequeño)",
        True,
        f"{n} -> {n // 4} filas",
    )
    inc.hito("incidente_activo")

    a = env.alertas()["DB-A05"]
    inc.registrar(
        "detección", "SELECT * FROM core.alertas_db() -> DB-A05", a["activa"] is True, a["detalle"]
    )
    inc.hito("detectado")

    with env.admin() as c:
        est = c.execute(
            "SELECT indice, tamano_bytes, filas_vivas, hinchazon_pct FROM vec.estado_hnsw() WHERE indice = %s",
            (indice,),
        ).fetchone()
        reconstruyendo = c.execute("SELECT count(*) FROM pg_stat_progress_create_index").fetchone()[
            0
        ]
    inc.registrar(
        "diagnóstico",
        "SELECT * FROM vec.estado_hnsw(): tamaño, filas vivas e hinchazón sobre la línea base",
        est is not None and est[3] > 30,
        f"{est[2]} filas vivas; hinchazón {est[3]} %",
    )
    inc.registrar(
        "diagnóstico",
        "comprobar que no hay ya una reconstrucción en curso (pg_stat_progress_create_index)",
        reconstruyendo == 0,
        f"{reconstruyendo} en curso",
    )
    inc.registrar(
        "contención",
        "no urgente: no se reconstruye en hora punta; el índice sigue siendo válido y las búsquedas funcionan",
        True,
        "se programa la ventana (docs/runbook-vectorial.md)",
    )
    inc.hito("contenido")

    t = time.perf_counter()
    with psycopg.connect(env.dsn(), autocommit=True) as c:
        c.execute("SET maintenance_work_mem = '256MB'")
        c.execute(f"REINDEX INDEX CONCURRENTLY vec.{indice}")
        dur = time.perf_counter() - t
        c.execute(
            "SELECT * FROM vec.medir_hnsw('despues', %s::numeric, %s::text)",
            (round(dur / 60, 2), indice),
        )
    inc.registrar(
        "resolución",
        "REINDEX INDEX CONCURRENTLY vec.doc_chunks_hnsw_ix + vec.medir_hnsw('despues', ...)",
        True,
        f"{round(dur, 1)} s",
    )
    inc.hito("resuelto")

    with env.admin() as c:
        valido = c.execute(
            "SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = ('vec.' || %s)::regclass",
            (indice,),
        ).fetchone()[0]
        restos = c.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'vec' AND (c.relname LIKE '%%\\_ccnew%%' OR c.relname LIKE '%%\\_ccold%%')"
        ).fetchone()[0]
        est = c.execute(
            "SELECT hinchazon_pct FROM vec.estado_hnsw() WHERE indice = %s", (indice,)
        ).fetchone()
    a = env.alertas()["DB-A05"]
    inc.registrar(
        "verificación",
        "índice válido, sin restos de reconstrucción, hinchazón bajo el umbral y DB-A05 apagada",
        bool(valido)
        and restos == 0
        and est[0] is not None
        and est[0] <= 30
        and a["activa"] is False,
        f"hinchazón {est[0]} %",
    )
    inc.hito("verificado")
    with psycopg.connect(env.dsn(), autocommit=True) as c:  # limpieza del ensayo
        c.execute("SET session_replication_role = replica")
        c.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (hnsw.TENANT,))
    return inc


# ---------------------------------------------------------------------------------------------------------
# I6 · Partición DEFAULT con filas
# ---------------------------------------------------------------------------------------------------------
def incidente_i6(env: Entorno) -> Incidente:
    import psycopg

    inc = Incidente("I6", "Partición DEFAULT con filas", "DB-A06")
    with env.admin() as c:
        c.execute("DELETE FROM core.notifications WHERE id LIKE 'nt_i6_%'")
        vacia = c.execute("SELECT count(*) FROM core.notifications_default").fetchone()[0]
    inc.registrar(
        "preparación",
        "línea base: la partición DEFAULT de core.notifications está vacía",
        vacia == 0,
        f"{vacia} filas",
    )
    inc.reiniciar()

    with env.admin() as c:
        for i in range(3):
            c.execute(
                "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, creada_en) VALUES (%s, 'org_demo', 'u_owner', 'sistema', 'fuera de rango', %s)",
                (f"nt_i6_{i}", dt.datetime(2040, 1, 10 + i, tzinfo=dt.UTC)),
            )
    inc.registrar(
        "provocación",
        "3 notificaciones con fecha fuera del rango de las particiones (enero de 2040)",
        True,
        "van a core.notifications_default",
    )
    inc.hito("incidente_activo")

    a = env.alertas()["DB-A06"]
    inc.registrar(
        "detección",
        "SELECT * FROM core.alertas_db() -> DB-A06",
        a["activa"] is True and "notifications_default" in a["detalle"],
        a["detalle"],
    )
    inc.hito("detectado")

    with env.admin() as c:
        rango = c.execute(
            "SELECT count(*), min(creada_en), max(creada_en) FROM core.notifications_default"
        ).fetchone()
        cfg = c.execute(
            "SELECT premake, automatic_maintenance, partition_interval FROM partman.part_config WHERE parent_table = 'core.notifications'"
        ).fetchone()
    inc.registrar(
        "diagnóstico",
        "rango de fechas de lo que cayó en la DEFAULT y configuración de pg_partman de la tabla",
        rango[0] == 3 and cfg is not None,
        f"{rango[0]} filas entre {rango[1]:%Y-%m-%d} y {rango[2]:%Y-%m-%d}; premake {cfg[0] if cfg else None}, mantenimiento automático {cfg[1] if cfg else None}",
    )
    inc.registrar(
        "contención",
        "la fecha está más allá de las particiones pre-creadas: no es un fallo del mantenimiento sino un dato fuera de rango; se decide si es legítimo (crear su partición) o erróneo (corregirlo en origen)",
        True,
        "decisión: legítimo",
    )
    inc.hito("contenido")

    # Resolución: crear la partición de ese mes y mover las filas, en una transacción corta con lock_timeout
    t = time.perf_counter()
    with psycopg.connect(env.dsn(), autocommit=False) as c:
        c.execute("SET LOCAL lock_timeout = '5s'")
        c.execute("ALTER TABLE core.notifications DETACH PARTITION core.notifications_default")
        c.execute(
            "SELECT partman.create_partition_time('core.notifications', ARRAY['2040-01-01'::timestamptz])"
        )
        c.execute("INSERT INTO core.notifications SELECT * FROM core.notifications_default")
        c.execute("TRUNCATE core.notifications_default")
        c.execute(
            "ALTER TABLE core.notifications ATTACH PARTITION core.notifications_default DEFAULT"
        )
        c.commit()
    inc.registrar(
        "resolución",
        "DETACH de la DEFAULT + partman.create_partition_time + INSERT SELECT + TRUNCATE + ATTACH DEFAULT, en una transacción con lock_timeout",
        True,
        f"{round(time.perf_counter() - t, 1)} s",
    )
    inc.hito("resuelto")

    with env.admin() as c:
        en_default = c.execute("SELECT count(*) FROM core.notifications_default").fetchone()[0]
        donde = c.execute(
            "SELECT tableoid::regclass::text, count(*) FROM core.notifications WHERE id LIKE 'nt_i6_%' GROUP BY 1"
        ).fetchall()
    a = env.alertas()["DB-A06"]
    inc.registrar(
        "verificación",
        "DEFAULT vacía, las 3 filas en su partición de 2040 y DB-A06 apagada",
        en_default == 0 and len(donde) == 1 and donde[0][1] == 3 and a["activa"] is False,
        f"{donde}",
    )
    inc.hito("verificado")
    with env.admin() as c:  # limpieza del ensayo
        c.execute("DELETE FROM core.notifications WHERE id LIKE 'nt_i6_%'")
    return inc


INCIDENTES = {
    "I1": incidente_i1,
    "I2": incidente_i2,
    "I3": incidente_i3,
    "I4": incidente_i4,
    "I5": incidente_i5,
    "I6": incidente_i6,
}


# ---------------------------------------------------------------------------------------------------------
# Acta
# ---------------------------------------------------------------------------------------------------------
def markdown(acta: dict[str, Any]) -> str:
    inc = acta["incidentes"]
    out = [
        "# Acta del ensayo de incidentes de base de datos (KC-DB-11-5)",
        "",
        f"Ejecutado: {acta['ejecutado_en']} · PostgreSQL {acta['version_postgres']} · {acta['pgbouncer']}",
        f"Resultado global: **{'LOS SEIS INCIDENTES SE ENSAYAN Y VERIFICAN' if acta['ok'] else 'HAY INCIDENTES QUE FALLAN'}** ({sum(i['ok'] for i in inc)}/{len(inc)})",
        "",
        "| Incidente | Alerta | Detectado | Contenido | Resuelto | Verificado | Resultado |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in inc:
        h = i["hitos_s"]
        out.append(
            f"| {i['id']} · {i['titulo']} | {i['alerta']} | {h.get('detectado', '-')} s | {h.get('contenido', '-')} s | {h.get('resuelto', '-')} s | {h.get('verificado', '-')} s | {'OK' if i['ok'] else 'FALLA'} |"
        )
    out += [
        "",
        "Los tiempos son segundos desde que el incidente está activo, en Docker local con datos mínimos: miden el procedimiento, no una promesa de producción.",
        "",
    ]
    for i in inc:
        out += [
            f"## {i['id']} · {i['titulo']} ({i['alerta']})",
            "",
            "| t (s) | Fase | Qué se hizo | Resultado | OK |",
            "|---|---|---|---|---|",
        ]
        for p in i["pasos"]:
            res = (
                str(p["resultado"]).replace("|", "/").replace("\n", " ")
                if p["resultado"] is not None
                else ""
            )
            out.append(
                f"| {'-' if p['t_s'] is None else p['t_s']} | {p['fase']} | {p['accion'].replace('|', '/')} | {res[:300]} | {'sí' if p['ok'] else '**NO**'} |"
            )
        out.append("")
    out += [
        "## Límites del ensayo",
        "",
        "* No se ensaya ningún canal de notificación: no hay Alertmanager en el repositorio. La «alerta» es la evaluación de `core.alertas_db()`, no un aviso a una persona.",
        "* El PgBouncer del ensayo no es el de producción (no está en el repositorio): se configura con valores de demostración (pool de 4, modo transacción).",
        "* Docker local, datos mínimos y un solo usuario provocando los incidentes: los tiempos miden el procedimiento.",
        "* La cadena rota (I3) se provoca con permisos de superusuario saltándose el disparador de inmutabilidad: es lo único que puede alterar un evento, y la restauración solo es legítima si el export WORM es el registrado y supera la verificación externa.",
        "* El acta la firma quien ejecuta el ensayo; no sustituye a la aprobación del responsable de operaciones.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--salida", type=Path)
    ap.add_argument(
        "--solo",
        action="append",
        choices=list(INCIDENTES),
        help="ensaya solo ese incidente (repetible)",
    )
    ap.add_argument("--conservar", action="store_true", help="no desmontar el entorno al terminar")
    ap.add_argument(
        "--reusar",
        action="store_true",
        help="reutilizar el entorno ya levantado (--conservar de una ejecución anterior)",
    )
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    env = _levantar(args.reusar)
    informes: list[dict[str, Any]] = []
    try:
        version = env.admin().execute("SHOW server_version").fetchone()[0]
        bouncer = pitr._docker("logs", NOMBRES["bouncer"], check=False)
        pgb = next(
            (
                ln.split("process up: ")[1].split(",")[0]
                for ln in (bouncer.stdout + bouncer.stderr).splitlines()
                if "process up" in ln
            ),
            "PgBouncer",
        )
        for ident in args.solo or list(INCIDENTES):
            print(f">> {ident}", flush=True)
            try:
                inc = INCIDENTES[ident](env)
            except Exception as exc:
                inc = Incidente(ident, "ERROR DEL ENSAYO", "-")
                inc.registrar(
                    "ensayo", "ejecución del incidente", False, f"{type(exc).__name__}: {exc}"
                )
            informes.append(inc.como_dict())
    finally:
        if not args.conservar:
            _desmontar()
    acta = {
        "ejecutado_en": dt.datetime.now(dt.UTC).isoformat(),
        "version_postgres": version,
        "pgbouncer": pgb,
        "duracion_s": round(time.perf_counter() - t0, 1),
        "ok": all(i["ok"] for i in informes),
        "incidentes": informes,
    }
    texto = markdown(acta)
    print(texto)
    if args.salida:
        args.salida.mkdir(parents=True, exist_ok=True)
        (args.salida / "acta.md").write_text(texto, encoding="utf-8")
        (args.salida / "acta.json").write_text(
            json.dumps(acta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    return 0 if acta["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
