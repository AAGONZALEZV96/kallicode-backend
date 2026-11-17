#!/usr/bin/env python3
"""Ensayo de restauración a un punto en el tiempo (PITR) con RPO y RTO medidos (KC-DB-11-1).

Todo ocurre en contenedores Docker desechables (imagen kallicode-postgres:16, la misma de db/postgres/Dockerfile):

  1. Instancia ORIGEN con archivado continuo de WAL (db/tools/archivar_wal.sh, archive_timeout = 60 s).
  2. Esquema completo (db/00..05 + migraciones) y datos: 3 tenants con cadena de auditoría real, claves de idempotencia.
  3. Copia base (pg_basebackup). Una carga continua escribe eventos de auditoría y apunta cada confirmación.
  4. Punto T elegido durante la carga; después un «accidente» (borrado de las claves de idempotencia) y más escrituras.
  5. DESASTRE: la instancia origen se mata sin avisar (SIGKILL) y se da por perdida con su disco.
  6. Dos restauraciones sobre instancias NUEVAS, cada una medida de principio a fin:
       A. PITR a T  -> debe recuperar lo anterior al accidente y nada posterior (RTO + corrección del punto).
       B. Hasta el final del WAL archivado -> mide la pérdida efectiva de datos (RPO real) y el RTO.
  7. En cada una: audit.verify_chain() de TODOS los tenants y core.validar_integridad() (los 26 chequeos).

Resultado: un acta (Markdown + JSON) con los tiempos reales. Los objetivos aprobados por el Architecture Board NO los
decide esta herramienta: el acta deja la aprobación pendiente (core.objetivos_recuperacion).

Uso:  python db/tools/ensayo_pitr.py --salida ./acta [--duracion 90] [--eventos-iniciales 3000] [--conservar]
Código de salida: 0 si se cumplen los objetivos y las verificaciones, 1 si no.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
IMAGEN = "kallicode-postgres:16"
BASE_DATOS = "kallicode_test"
RPO_OBJETIVO_S = 5 * 60
RTO_OBJETIVO_S = 60 * 60
ARCHIVE_TIMEOUT_S = 60
TENANTS = 3


# ---------------------------------------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------------------------------------
def _run(
    args: list[str], entrada: str | None = None, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(
        args,
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args[:4])}... falló ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}"
        )
    return r


def _docker(*args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
    return _run(["docker", *args], **kw)


def _psql(contenedor: str, sql: str, usuario: str = "postgres", base: str = BASE_DATOS) -> str:
    r = _docker(
        "exec",
        contenedor,
        "psql",
        "-U",
        usuario,
        "-d",
        base,
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-c",
        sql,
    )
    return r.stdout.strip()


def _args_postgres(archivar: bool) -> list[str]:
    if archivar:
        return [
            "postgres",
            "-c", "shared_preload_libraries=pg_stat_statements,pg_partman_bgw,pg_cron",
            "-c", "pg_partman_bgw.interval=3600",
            "-c", f"pg_partman_bgw.dbname={BASE_DATOS}",
            "-c", "pg_partman_bgw.role=kallicode_admin",
            "-c", f"cron.database_name={BASE_DATOS}",
            "-c", "wal_level=replica",
            "-c", "archive_mode=on",
            "-c", 'archive_command=sh /usr/local/bin/archivar_wal.sh "%p" "%f"',
            "-c", f"archive_timeout={ARCHIVE_TIMEOUT_S}",
        ]  # fmt: skip
    # Instancia RESTAURADA: sin pg_cron ni pg_partman_bgw hasta verificar (si no, dispararían las purgas y el
    # mantenimiento programado sobre una copia que todavía no se ha comprobado) y sin archivar.
    return [
        "postgres",
        "-c",
        "shared_preload_libraries=pg_stat_statements",
        "-c",
        "archive_mode=off",
    ]


def _esperar_listo(contenedor: str, segundos: int = 180) -> float:
    """Espera a que la instancia acepte conexiones TCP finales (no el servidor temporal de initdb)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < segundos:
        logs = _docker("logs", contenedor, check=False)
        texto = logs.stdout + logs.stderr
        listos = texto.count("database system is ready to accept connections")
        init = "PostgreSQL init process complete" in texto
        if listos >= (2 if init else 1):
            r = _docker(
                "exec",
                contenedor,
                "psql",
                "-U",
                "postgres",
                "-d",
                BASE_DATOS,
                "-At",
                "-c",
                "SELECT NOT pg_is_in_recovery()",
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip() == "t":
                return time.perf_counter() - t0
        time.sleep(0.5)
    raise TimeoutError(f"{contenedor} no quedó operativo en {segundos} s")


def _puerto(contenedor: str) -> int:
    salida = _docker("port", contenedor, "5432/tcp").stdout.strip().splitlines()[0]
    return int(salida.rsplit(":", 1)[1])


# ---------------------------------------------------------------------------------------------------------
# Preparación del origen
# ---------------------------------------------------------------------------------------------------------
def _crear_origen(ids: dict[str, str]) -> None:
    _docker(
        "run",
        "--rm",
        "-v",
        f"{ids['arch']}:/pitr",
        IMAGEN,
        "sh",
        "-c",
        "mkdir -p /pitr/wal /pitr/base && chown -R postgres:postgres /pitr",
    )
    _docker(
        "create", "--name", ids["orig"], "-e", "POSTGRES_PASSWORD=dev", "-e", f"POSTGRES_DB={BASE_DATOS}",
        "-v", f"{ids['vol_orig']}:/var/lib/postgresql/data", "-v", f"{ids['arch']}:/pitr",
        "-p", "127.0.0.1::5432", IMAGEN, *_args_postgres(True),
    )  # fmt: skip
    _docker(
        "cp",
        str(RAIZ / "db" / "tools" / "archivar_wal.sh"),
        f"{ids['orig']}:/usr/local/bin/archivar_wal.sh",
    )
    _docker("start", ids["orig"])
    _esperar_listo(ids["orig"])


def _esquema(ids: dict[str, str], puerto: int) -> None:
    c = ids["orig"]
    for _ in range(2):  # 00_roles.sql se ejecuta dos veces, como en el CI (idempotente)
        _docker("exec", "-i", c, "psql", "-U", "postgres", "-d", BASE_DATOS, "-v", "ON_ERROR_STOP=1", "-q",
                "-v", "pw_app=dev", "-v", "pw_worker=dev", "-v", "pw_admin=dev",
                entrada=(RAIZ / "db" / "00_roles.sql").read_text(encoding="utf-8"))  # fmt: skip
    _docker("exec", "-i", c, "psql", "-U", "postgres", "-d", BASE_DATOS, "-v", "ON_ERROR_STOP=1", "-q",
            entrada=(RAIZ / "db" / "00_extensiones.sql").read_text(encoding="utf-8"))  # fmt: skip
    for f in sorted((RAIZ / "db").glob("0[1-9]_*.sql")):
        _docker("exec", "-i", "-e", "PGPASSWORD=dev", c, "psql", "-h", "localhost", "-U", "kallicode_admin", "-d", BASE_DATOS,
                "-v", "ON_ERROR_STOP=1", "-q", entrada=f.read_text(encoding="utf-8"))  # fmt: skip
    env = {
        **os.environ,
        "KC_DATABASE_URL_ADMIN": f"postgresql+asyncpg://kallicode_admin:dev@127.0.0.1:{puerto}/{BASE_DATOS}",  # pragma: allowlist secret - credencial del contenedor efímero de ensayo
    }
    r = subprocess.run(
        [sys.executable, str(RAIZ / "packages" / "core" / "migrate.py")], env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, cwd=RAIZ,
    )  # fmt: skip
    if r.returncode != 0:
        raise RuntimeError(f"migrate.py falló: {r.stdout[-500:]} {r.stderr[-500:]}")


def _sembrar(contenedor: str, eventos: int) -> list[str]:
    s = uuid.uuid4().hex[:6]
    tenants = [f"org_pitr_{i}_{s}" for i in range(TENANTS)]
    sql = []
    for t in tenants:
        sql.append(f"INSERT INTO core.organizations (id, nombre) VALUES ('{t}', '{t}');")
        sql.append(
            f"INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el) "
            f"VALUES ('sub_{t}', '{t}', 'growth', current_date - 5, current_date + 25);"
        )
        sql.append(
            f"INSERT INTO core.users (id, tenant_id, email, nombre) VALUES ('us_{t}', '{t}', '{t}@pitr.dev', 'U');"
        )
    sql.append(
        "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code) "
        f"SELECT 'k' || g, '{tenants[0]}', 'ep.pitr', '{{}}', 200 FROM generate_series(1, 100) g;"
    )
    por_tenant = max(eventos // TENANTS, 1)
    for t in tenants:
        sql.append(
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
            f"SELECT '{t}', 'sistema', 'seed_pitr', 'e' || g, repeat('0', 64), repeat('a', 64), '10.0.0.7', 'pitr/1', 'req-{t}-' || g "
            f"FROM generate_series(1, {por_tenant}) g;"
        )
    _docker(
        "exec",
        "-i",
        contenedor,
        "psql",
        "-U",
        "postgres",
        "-d",
        BASE_DATOS,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
        entrada="\n".join(sql),
    )
    return tenants


# ---------------------------------------------------------------------------------------------------------
# Carga continua
# ---------------------------------------------------------------------------------------------------------
class Carga:
    """Escribe un evento de auditoría cada ~0,2 s y apunta cada confirmación (id y hora de la base)."""

    def __init__(self, dsn: str, tenants: list[str]) -> None:
        self.dsn, self.tenants = dsn, tenants
        self.confirmados: list[tuple[int, dt.datetime]] = []
        self.cerrojo = threading.Lock()  # permite fijar T sin transacciones a medias
        self.parar = threading.Event()
        self.error: str | None = None
        self.hilo = threading.Thread(target=self._bucle, daemon=True)

    def _bucle(self) -> None:
        import psycopg

        try:
            with psycopg.connect(self.dsn, autocommit=True) as c:
                i = 0
                while not self.parar.is_set():
                    t = self.tenants[i % len(self.tenants)]
                    i += 1
                    with self.cerrojo:
                        fila = c.execute(
                            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
                            "VALUES (%s, 'sistema', 'carga_pitr', %s, %s, %s, '10.0.0.7', 'pitr/1', %s) RETURNING id, creado_en",
                            (
                                t,
                                f"c{i}",
                                "0" * 64,
                                "a" * 64,
                                f"req-carga-{i}-{uuid.uuid4().hex[:6]}",
                            ),
                        ).fetchone()
                    if fila is not None:
                        self.confirmados.append((int(fila[0]), fila[1]))
                    time.sleep(0.2)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def iniciar(self) -> None:
        self.hilo.start()

    def detener(self) -> None:
        self.parar.set()
        self.hilo.join(timeout=10)


# ---------------------------------------------------------------------------------------------------------
# Restauración
# ---------------------------------------------------------------------------------------------------------
def _restaurar(ids: dict[str, str], nombre: str, objetivo: str | None) -> dict[str, Any]:
    """Restaura la copia base y el WAL archivado en una instancia NUEVA y mide hasta que está operativa."""
    vol, cont = ids[f"vol_{nombre}"], ids[nombre]
    t0 = (
        time.perf_counter()
    )  # empieza el procedimiento: la copia base y el WAL archivado existen, el origen no
    conf = "restore_command = 'cp /pitr/wal/%f %p'\nrecovery_target_action = 'promote'\n"
    if objetivo:
        conf += f"recovery_target_time = '{objetivo}'\nrecovery_target_inclusive = on\n"
    _docker(
        "run", "--rm", "-e", f"CONF={conf}", "-v", f"{vol}:/var/lib/postgresql/data", "-v", f"{ids['arch']}:/pitr:ro", IMAGEN, "sh", "-c",
        "cp -a /pitr/base/base1/. /var/lib/postgresql/data/ && rm -f /var/lib/postgresql/data/postmaster.pid "
        "&& printf '%s\\n' \"$CONF\" >> /var/lib/postgresql/data/postgresql.auto.conf && touch /var/lib/postgresql/data/recovery.signal "
        "&& chown -R postgres:postgres /var/lib/postgresql/data && chmod 700 /var/lib/postgresql/data",
    )  # fmt: skip
    t_copia = time.perf_counter() - t0
    _docker("run", "-d", "--name", cont, "-v", f"{vol}:/var/lib/postgresql/data", "-v", f"{ids['arch']}:/pitr:ro",
            "-e", "POSTGRES_PASSWORD=dev", "-p", "127.0.0.1::5432", IMAGEN, *_args_postgres(False))  # fmt: skip
    _esperar_listo(cont)
    rto = time.perf_counter() - t0
    logs = _docker("logs", cont, check=False)
    texto = logs.stdout + logs.stderr
    relevantes = [
        linea.split("LOG:", 1)[-1].strip()
        for linea in texto.splitlines()
        if any(
            k in linea
            for k in (
                "recovery stopping",
                "last completed transaction",
                "archive recovery complete",
                "consistent recovery state",
            )
        )
    ]
    return {
        "contenedor": cont,
        "objetivo_tiempo": objetivo,
        "rto_segundos": round(rto, 1),
        "copia_base_segundos": round(t_copia, 1),
        "recuperacion_segundos": round(rto - t_copia, 1),
        "segmentos_wal_reproducidos": texto.count("restored log file"),
        "registro_recuperacion": relevantes[-6:],
    }


def _verificar(contenedor: str) -> dict[str, Any]:
    """audit.verify_chain() de todos los tenants y los chequeos de core.validar_integridad()."""
    t0 = time.perf_counter()
    cadenas = _psql(
        contenedor,
        "SELECT count(*), count(*) FILTER (WHERE NOT v.integra) FROM audit.chain_heads h "
        "CROSS JOIN LATERAL audit.verify_chain(h.tenant_id) v",
    ).split("|")
    chequeos = _psql(
        contenedor, "SELECT count(*), coalesce(sum(problemas), 0) FROM core.validar_integridad()"
    ).split("|")
    return {
        "tenants_con_cadena": int(cadenas[0]),
        "cadenas_rotas": int(cadenas[1]),
        "chequeos": int(chequeos[0]),
        "hallazgos": int(chequeos[1]),
        "segundos": round(time.perf_counter() - t0, 1),
        "ok": int(cadenas[0]) >= TENANTS
        and int(cadenas[1]) == 0
        and int(chequeos[0]) == 26
        and int(chequeos[1]) == 0,
    }


# ---------------------------------------------------------------------------------------------------------
# El ensayo
# ---------------------------------------------------------------------------------------------------------
def ejecutar(
    duracion: int = 90, eventos_iniciales: int = 3000, conservar: bool = False
) -> dict[str, Any]:
    run = uuid.uuid4().hex[:6]
    ids = {
        "arch": f"kc-pitr-{run}-arch", "vol_orig": f"kc-pitr-{run}-orig-data", "orig": f"kc-pitr-{run}-orig",
        "vol_a": f"kc-pitr-{run}-vol-a", "a": f"kc-pitr-{run}-a", "vol_b": f"kc-pitr-{run}-vol-b", "b": f"kc-pitr-{run}-b",
    }  # fmt: skip
    acta: dict[str, Any] = {"ejecutado_en": dt.datetime.now(dt.UTC).isoformat(), "pasos": {}}
    carga: Carga | None = None
    try:
        if _docker("image", "inspect", IMAGEN, check=False).returncode != 0:
            _docker("build", "-t", IMAGEN, str(RAIZ / "db" / "postgres"), timeout=1800)
        acta["version_postgres"] = None

        t = time.perf_counter()
        _crear_origen(ids)
        puerto = _puerto(ids["orig"])
        _esquema(ids, puerto)
        tenants = _sembrar(ids["orig"], eventos_iniciales)
        acta["version_postgres"] = _psql(ids["orig"], "SHOW server_version")
        acta["pasos"]["preparacion_origen_s"] = round(time.perf_counter() - t, 1)
        estado0 = _psql(
            ids["orig"],
            "SELECT archivado_activado, wal_level, archive_timeout_s FROM core.estado_archivado_wal()",
        )
        acta["archivado"] = {"estado_inicial": estado0}

        # copia base
        t = time.perf_counter()
        _docker(
            "exec",
            "-u",
            "postgres",
            ids["orig"],
            "pg_basebackup",
            "-U",
            "postgres",
            "-D",
            "/pitr/base/base1",
            "-Fp",
            "-X",
            "stream",
            "-c",
            "fast",
        )
        acta["copia_base"] = {
            "segundos": round(time.perf_counter() - t, 1),
            "tamano_mb": int(
                _docker(
                    "exec", ids["orig"], "sh", "-c", "du -sm /pitr/base/base1 | cut -f1"
                ).stdout.split()[0]
            ),
            "tamano_base_datos_mb": round(
                int(_psql(ids["orig"], "SELECT pg_database_size(current_database())")) / 1048576, 1
            ),
        }

        dsn = f"postgresql://postgres:dev@127.0.0.1:{puerto}/{BASE_DATOS}"  # pragma: allowlist secret - credencial del contenedor efímero de ensayo
        carga = Carga(dsn, tenants)
        carga.iniciar()
        time.sleep(max(duracion // 3, 5))

        # punto T: se pausa la carga un instante para que no haya transacciones a medias alrededor de T
        with carga.cerrojo:
            T = _psql(
                ids["orig"],
                "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'",
            )
        time.sleep(3)
        antes_de_T = int(
            _psql(ids["orig"], f"SELECT count(*) FROM audit.audit_events WHERE creado_en <= '{T}'")
        )
        claves_en_T = int(_psql(ids["orig"], "SELECT count(*) FROM core.idempotency_keys"))
        # accidente (lo que se quiere deshacer con PITR) y cierre del segmento de WAL que lo contiene
        _psql(ids["orig"], "DELETE FROM core.idempotency_keys")
        segmento = _psql(ids["orig"], "SELECT pg_walfile_name(pg_switch_wal() - 1)")
        t_espera = time.perf_counter()
        while time.perf_counter() - t_espera < 180:
            ultimo = _psql(
                ids["orig"], "SELECT coalesce(last_archived_wal, '') FROM pg_stat_archiver"
            )
            if ultimo >= segmento:
                break
            time.sleep(1)
        else:
            raise TimeoutError("el segmento de WAL del accidente no llegó a archivarse")

        time.sleep(max(duracion - duracion // 3, 5))
        archiver = _psql(
            ids["orig"], "SELECT archived_count, failed_count FROM pg_stat_archiver"
        ).split("|")
        acta["archivado"].update(
            {"segmentos_archivados": int(archiver[0]), "fallos": int(archiver[1])}
        )

        # DESASTRE
        _docker("kill", ids["orig"])
        carga.detener()
        confirmados = list(carga.confirmados)
        ultima_hora = confirmados[-1][1]
        acta["desastre"] = {
            "eventos_confirmados_en_la_carga": len(confirmados),
            "ultimo_evento_confirmado": ultima_hora.isoformat(),
        }
        wal = _docker(
            "run",
            "--rm",
            "-v",
            f"{ids['arch']}:/pitr:ro",
            IMAGEN,
            "sh",
            "-c",
            "ls /pitr/wal | wc -l; du -sm /pitr/wal | cut -f1",
        ).stdout.split()
        acta["archivado"]["segmentos_en_archivo"], acta["archivado"]["tamano_archivo_mb"] = (
            int(wal[0]),
            int(wal[1]),
        )

        # A. PITR a T
        ra = _restaurar(ids, "a", T)
        ra["verificacion"] = _verificar(ids["a"])
        ra["eventos_esperados_hasta_T"] = antes_de_T
        ra["eventos_restaurados"] = int(_psql(ids["a"], "SELECT count(*) FROM audit.audit_events"))
        ra["eventos_posteriores_a_T"] = int(
            _psql(ids["a"], f"SELECT count(*) FROM audit.audit_events WHERE creado_en > '{T}'")
        )
        ra["claves_idempotencia_esperadas"] = claves_en_T
        ra["claves_idempotencia_restauradas"] = int(
            _psql(ids["a"], "SELECT count(*) FROM core.idempotency_keys")
        )
        ra["punto_correcto"] = (
            ra["eventos_restaurados"] == antes_de_T
            and ra["eventos_posteriores_a_T"] == 0
            and ra["claves_idempotencia_restauradas"] == claves_en_T
        )
        ra["objetivo_tiempo"] = T
        acta["restauracion_a_punto_T"] = ra

        # B. hasta el final del WAL archivado: pérdida efectiva de datos
        rb = _restaurar(ids, "b", None)
        rb["verificacion"] = _verificar(ids["b"])
        max_id, ultima_restaurada = _psql(
            ids["b"],
            "SELECT max(id), to_char(max(creado_en) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') FROM audit.audit_events",
        ).split("|")
        perdidos = [e for e in confirmados if e[0] > int(max_id)]
        ultima_r = dt.datetime.strptime(ultima_restaurada, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=dt.UTC
        )
        rb["eventos_confirmados_perdidos"] = len(perdidos)
        rb["perdida_efectiva_segundos"] = round(
            max((ultima_hora - ultima_r).total_seconds(), 0.0), 1
        )
        rb["ultimo_evento_restaurado"] = ultima_r.isoformat()
        acta["restauracion_hasta_el_final_del_wal"] = rb
        acta["objetivos_medidos"] = {
            "rpo_objetivo_s": RPO_OBJETIVO_S, "rpo_medido_s": rb["perdida_efectiva_segundos"],
            "rto_objetivo_s": RTO_OBJETIVO_S, "rto_medido_a_s": ra["rto_segundos"], "rto_medido_b_s": rb["rto_segundos"],
        }  # fmt: skip
        acta["cumple"] = bool(
            rb["perdida_efectiva_segundos"] <= RPO_OBJETIVO_S
            and max(ra["rto_segundos"], rb["rto_segundos"]) <= RTO_OBJETIVO_S
            and ra["punto_correcto"]
            and ra["verificacion"]["ok"]
            and rb["verificacion"]["ok"]
        )
    finally:
        if carga is not None:
            carga.detener()
        if not conservar:
            for c in (ids["orig"], ids["a"], ids["b"]):
                _docker("rm", "-f", c, check=False)
            for v in (ids["arch"], ids["vol_orig"], ids["vol_a"], ids["vol_b"]):
                _docker("volume", "rm", "-f", v, check=False)
    return acta


def markdown(a: dict[str, Any]) -> str:
    ra, rb, om = (
        a["restauracion_a_punto_T"],
        a["restauracion_hasta_el_final_del_wal"],
        a["objetivos_medidos"],
    )
    si = lambda b: "SÍ" if b else "NO"
    return f"""# Acta del ensayo de restauración PITR (KC-DB-11-1)

Ejecutado: {a["ejecutado_en"]} · PostgreSQL {a["version_postgres"]} · imagen {IMAGEN}
Resultado global: **{"CUMPLE" if a["cumple"] else "NO CUMPLE"}** los objetivos propuestos (RPO ≤ 5 min, RTO ≤ 60 min)

## Objetivos y medición real

| Objetivo | Propuesto | Medido | ¿Cumple? |
|---|---|---|---|
| RPO (pérdida efectiva de datos) | ≤ {om["rpo_objetivo_s"]} s | {om["rpo_medido_s"]} s ({rb["eventos_confirmados_perdidos"]} eventos confirmados perdidos) | {si(om["rpo_medido_s"] <= om["rpo_objetivo_s"])} |
| RTO, restauración a un punto T | ≤ {om["rto_objetivo_s"]} s | {om["rto_medido_a_s"]} s | {si(om["rto_medido_a_s"] <= om["rto_objetivo_s"])} |
| RTO, hasta el final del WAL | ≤ {om["rto_objetivo_s"]} s | {om["rto_medido_b_s"]} s | {si(om["rto_medido_b_s"] <= om["rto_objetivo_s"])} |

El RTO cuenta desde que empieza el procedimiento de restauración (la copia base y el WAL archivado existen, el origen
no) hasta que la instancia nueva acepta conexiones fuera de recuperación; **no** incluye la verificación posterior ni
detectar el desastre ni decidir restaurar.

## Datos del ensayo

* Base de datos: {a["copia_base"]["tamano_base_datos_mb"]} MB; copia base {a["copia_base"]["tamano_mb"]} MB en {a["copia_base"]["segundos"]} s.
* Archivado: {a["archivado"]["segmentos_en_archivo"]} segmentos de WAL archivados ({a["archivado"]["tamano_archivo_mb"]} MB), {a["archivado"]["fallos"]} fallos; archive_timeout = {ARCHIVE_TIMEOUT_S} s.
* Carga durante el ensayo: {a["desastre"]["eventos_confirmados_en_la_carga"]} eventos de auditoría confirmados; último a las {a["desastre"]["ultimo_evento_confirmado"]}.

## A. Restauración a un punto en el tiempo (T = {ra["objetivo_tiempo"]})

* Copia base: {ra["copia_base_segundos"]} s · reproducción del WAL y arranque: {ra["recuperacion_segundos"]} s · segmentos reproducidos: {ra["segmentos_wal_reproducidos"]}.
* Eventos de auditoría hasta T: esperados {ra["eventos_esperados_hasta_T"]}, restaurados {ra["eventos_restaurados"]}, posteriores a T: {ra["eventos_posteriores_a_T"]}.
* Claves de idempotencia (borradas por el «accidente» posterior a T): esperadas {ra["claves_idempotencia_esperadas"]}, restauradas {ra["claves_idempotencia_restauradas"]}.
* **Punto correcto: {si(ra["punto_correcto"])}.**
* audit.verify_chain(): {ra["verificacion"]["tenants_con_cadena"]} tenants, {ra["verificacion"]["cadenas_rotas"]} cadenas rotas · core.validar_integridad(): {ra["verificacion"]["chequeos"]} chequeos, {ra["verificacion"]["hallazgos"]} hallazgos ({ra["verificacion"]["segundos"]} s).

## B. Restauración hasta el final del WAL archivado (desastre: origen perdido con SIGKILL)

* Copia base: {rb["copia_base_segundos"]} s · reproducción del WAL y arranque: {rb["recuperacion_segundos"]} s · segmentos reproducidos: {rb["segmentos_wal_reproducidos"]}.
* Último evento restaurado: {rb["ultimo_evento_restaurado"]} · confirmados perdidos: {rb["eventos_confirmados_perdidos"]} · pérdida efectiva: {rb["perdida_efectiva_segundos"]} s.
* audit.verify_chain(): {rb["verificacion"]["tenants_con_cadena"]} tenants, {rb["verificacion"]["cadenas_rotas"]} cadenas rotas · core.validar_integridad(): {rb["verificacion"]["chequeos"]} chequeos, {rb["verificacion"]["hallazgos"]} hallazgos ({rb["verificacion"]["segundos"]} s).

## Aprobación (pendiente)

Los objetivos RPO ≤ 5 min y RTO ≤ 60 min están en core.objetivos_recuperacion como **propuestos**. Este ensayo no los
aprueba: lo hace el Architecture Board (core.aprobar_objetivos_recuperacion).

| Aprobado por | Fecha | Referencia del acta |
|---|---|---|
| _pendiente_ | _pendiente_ | _pendiente_ |

## Límites de esta medición

* Docker local con un volumen mínimo y el archivo de WAL en un volumen del mismo anfitrión: los tiempos son del procedimiento, **no** una promesa para producción con otro volumen, red y almacenamiento.
* El origen se mata con SIGKILL pero su disco no se usa: se supone perdido. Con el disco accesible se podría recuperar más WAL (RPO menor).
* La pérdida depende de cuándo se corta: hasta archive_timeout ({ARCHIVE_TIMEOUT_S} s) más el tiempo de archivado. Una sola ejecución mide un punto de esa distribución, no su máximo.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument(
        "--duracion",
        type=int,
        default=90,
        help="segundos totales de carga continua (por defecto 90)",
    )
    ap.add_argument("--eventos-iniciales", type=int, default=3000)
    ap.add_argument(
        "--conservar", action="store_true", help="no borrar contenedores ni volúmenes al terminar"
    )
    args = ap.parse_args(argv)

    acta = ejecutar(args.duracion, args.eventos_iniciales, args.conservar)
    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "acta.json").write_text(
        json.dumps(acta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    texto = markdown(acta)
    (args.salida / "acta.md").write_text(texto, encoding="utf-8")
    print(texto)
    return 0 if acta["cumple"] else 1


if __name__ == "__main__":
    sys.exit(main())
