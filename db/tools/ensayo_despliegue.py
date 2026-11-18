#!/usr/bin/env python3
"""Ensayo de despliegue de GA sobre un staging simulado con base persistente (KC-DB-12-5).

Reproduce, en contenedores Docker con volúmenes persistentes, el despliegue completo del esquema:

  1. «Release anterior»: se aplica el esquema hasta una migración base (--desde) y se cargan datos (3 tenants con cadena de
     auditoría real). La instancia se REINICIA para comprobar que la base es persistente.
  2. Backup previo: copia base (pg_basebackup) con el WAL archivado de forma continua, y se anota el instante T0.
  3. Migraciones como paso SEPARADO: se aplican las que faltan, en orden, con el mismo runner que usa el CI; se cronometra cada
     una contra el presupuesto.
  4. Smoke de release: db/tools/smoke_release.sql (versión, migraciones, chequeos en cero, cadena íntegra, cero tablas sin
     política) con el rol administrador, y db/tools/smoke_post_deploy.sql con el rol de la aplicación. Se comprueba además que
     el smoke FALLA con una versión equivocada (control negativo).
  5. Segundo ensayo de restauración: el servidor se pierde y se restaura en instancias NUEVAS (a) al estado posterior al
     despliegue y (b) al instante T0 (la vuelta atrás), midiendo el RTO contra el objetivo de core.objetivos_recuperacion y
     verificando en cada una la cadena de auditoría y los 26 chequeos.

Resultado: un acta (Markdown y JSON) con los tiempos reales y un bloque de firmas que queda PENDIENTE: este ensayo no firma ni
aprueba nada (Product Owner, Architecture Board y Tech Lead de Testing firman el acta de aceptación).

Qué NO es: no es el staging real (no hay uno en el repositorio): es una simulación local con datos mínimos. Sus tiempos miden el
procedimiento, no una promesa para un volumen de producción.

Uso:  python db/tools/ensayo_despliegue.py --salida ./acta_ga [--desde 060_vigencia_embeddings_hnsw.sql] [--conservar]
Código de salida: 0 si se cumplen los criterios técnicos, 1 si no (las firmas no cuentan: son humanas).
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
MIGRACIONES = RAIZ / "db" / "migrations"
DESDE_POR_DEFECTO = "060_vigencia_embeddings_hnsw.sql"
# Presupuesto PROPUESTO (nadie lo había definido): unas veinte veces lo medido en Docker local con las 67 migraciones
# (6,7 s en total y 2,3 s la más lenta). Hay que ratificarlo con el volumen de staging real.
PRESUPUESTO_TOTAL_S = 120.0
PRESUPUESTO_MIGRACION_S = 30.0


def _cargar(nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        nombre, Path(__file__).resolve().parent / f"{nombre}.py"
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


pitr = _cargar(
    "ensayo_pitr"
)  # copia base, WAL archivado, restauración y verificación ya probados en KC-DB-11-1


# ---------------------------------------------------------------------------------------------------------
# Presupuesto y datos del artefacto (puros: se prueban sin Docker)
# ---------------------------------------------------------------------------------------------------------
def migraciones_del_artefacto() -> list[str]:
    return sorted(p.name for p in MIGRACIONES.glob("*.sql"))


def datos_del_artefacto() -> dict[str, Any]:
    """Lo que el smoke debe encontrar en la base desplegada: etiqueta, última migración y cuántas trae este SHA."""
    snapshot = json.loads((RAIZ / "db" / "esquema_v2.snapshot.json").read_text(encoding="utf-8"))
    migraciones = migraciones_del_artefacto()
    return {
        "version_esperada": snapshot.get("version") or snapshot.get("etiqueta"),
        "ultima_migracion": migraciones[-1],
        "migraciones": len(migraciones),
    }


def evaluar_presupuesto(
    duraciones_ms: dict[str, int],
    total_pasos_s: float,
    por_migracion_s: float = PRESUPUESTO_MIGRACION_S,
    total_s: float = PRESUPUESTO_TOTAL_S,
) -> dict[str, Any]:
    """Compara cada migración y el total con el presupuesto. `duraciones_ms` es el ledger: archivo -> milisegundos."""
    suma_s = sum(duraciones_ms.values()) / 1000
    lentas = {
        a: round(ms / 1000, 2)
        for a, ms in sorted(duraciones_ms.items())
        if ms / 1000 > por_migracion_s
    }
    mas_lenta = max(duraciones_ms.items(), key=lambda kv: kv[1]) if duraciones_ms else ("-", 0)
    return {
        "migraciones": len(duraciones_ms),
        "suma_s": round(suma_s, 2),
        "mas_lenta": mas_lenta[0],
        "mas_lenta_s": round(mas_lenta[1] / 1000, 2),
        "paso_de_despliegue_s": round(total_pasos_s, 2),
        "presupuesto_total_s": total_s,
        "presupuesto_por_migracion_s": por_migracion_s,
        "migraciones_fuera_de_presupuesto": lentas,
        "dentro_del_presupuesto": not lentas and suma_s <= total_s and total_pasos_s <= total_s,
    }


# ---------------------------------------------------------------------------------------------------------
# Pasos con Docker
# ---------------------------------------------------------------------------------------------------------
def _migrar(puerto: int, directorio: Path | None = None) -> float:
    env = {
        **os.environ,
        "KC_DATABASE_URL_ADMIN": f"postgresql+asyncpg://kallicode_admin:dev@127.0.0.1:{puerto}/{pitr.BASE_DATOS}",  # pragma: allowlist secret - credencial del contenedor efímero de ensayo
    }
    args = [sys.executable, str(RAIZ / "packages" / "core" / "migrate.py")] + (
        ["--dir", str(directorio)] if directorio else []
    )
    t0 = time.perf_counter()
    r = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=RAIZ,
    )
    if r.returncode != 0:
        raise RuntimeError(f"migrate.py falló: {r.stdout[-600:]} {r.stderr[-600:]}")
    return time.perf_counter() - t0


def _base_inicial(ids: dict[str, str]) -> None:
    """Roles, extensiones y los scripts basales 01..05, como en el CI."""
    c = ids["orig"]
    d = pitr._docker
    for _ in range(2):
        d("exec", "-i", c, "psql", "-U", "postgres", "-d", pitr.BASE_DATOS, "-v", "ON_ERROR_STOP=1", "-q",
          "-v", "pw_app=dev", "-v", "pw_worker=dev", "-v", "pw_admin=dev",
          entrada=(RAIZ / "db" / "00_roles.sql").read_text(encoding="utf-8"))  # fmt: skip
    d("exec", "-i", c, "psql", "-U", "postgres", "-d", pitr.BASE_DATOS, "-v", "ON_ERROR_STOP=1", "-q",
      entrada=(RAIZ / "db" / "00_extensiones.sql").read_text(encoding="utf-8"))  # fmt: skip
    for f in sorted((RAIZ / "db").glob("0[1-9]_*.sql")):
        d("exec", "-i", "-e", "PGPASSWORD=dev", c, "psql", "-h", "localhost", "-U", "kallicode_admin", "-d", pitr.BASE_DATOS,
          "-v", "ON_ERROR_STOP=1", "-q", entrada=f.read_text(encoding="utf-8"))  # fmt: skip


def _smoke(
    contenedor: str, usuario: str, archivo: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """Ejecuta un script de smoke con psql dentro del contenedor y devuelve código de salida y salida."""
    args = [
        "exec",
        "-i",
        "-e",
        "PGPASSWORD=dev",
        contenedor,
        "psql",
        "-h",
        "localhost",
        "-U",
        usuario,
        "-d",
        pitr.BASE_DATOS,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    for k, v in variables.items():
        args += ["-v", f"{k}={v}"]
    r = pitr._docker(
        *args, entrada=(RAIZ / "db" / "tools" / archivo).read_text(encoding="utf-8"), check=False
    )
    return {"codigo": r.returncode, "salida": (r.stdout + r.stderr).strip()}


def _filas_smoke(salida: str) -> list[str]:
    return [
        ln.strip()
        for ln in salida.splitlines()
        if "|" in ln and ("OK" in ln or "FALLA" in ln) and "estado" not in ln
    ]


def ejecutar(
    desde: str = DESDE_POR_DEFECTO, eventos: int = 3000, conservar: bool = False
) -> dict[str, Any]:
    run = uuid.uuid4().hex[:6]
    ids = {
        "arch": f"kc-ga-{run}-arch", "vol_orig": f"kc-ga-{run}-datos", "orig": f"kc-ga-{run}-staging",
        "vol_a": f"kc-ga-{run}-vol-a", "a": f"kc-ga-{run}-a", "vol_b": f"kc-ga-{run}-vol-b", "b": f"kc-ga-{run}-b",
    }  # fmt: skip
    artefacto = datos_del_artefacto()
    acta: dict[str, Any] = {
        "ejecutado_en": dt.datetime.now(dt.UTC).isoformat(),
        "artefacto": artefacto,
        "desde": desde,
        "pasos": {},
    }
    t_ensayo = time.perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix="kc_ga_"))
    try:
        if pitr._docker("image", "inspect", pitr.IMAGEN, check=False).returncode != 0:
            pitr._docker("build", "-t", pitr.IMAGEN, str(RAIZ / "db" / "postgres"), timeout=1800)

        # 1. release anterior + datos + reinicio (base persistente)
        t = time.perf_counter()
        pitr._crear_origen(ids)
        puerto = pitr._puerto(ids["orig"])
        _base_inicial(ids)
        base_dir = tmp / "base"
        base_dir.mkdir()
        previas = [m for m in migraciones_del_artefacto() if m <= desde]
        for m in previas:
            shutil.copy(MIGRACIONES / m, base_dir / m)
        _migrar(puerto, base_dir)
        tenants = pitr._sembrar(ids["orig"], eventos)
        antes = int(pitr._psql(ids["orig"], "SELECT count(*) FROM audit.audit_events"))
        acta["version_postgres"] = pitr._psql(ids["orig"], "SHOW server_version")
        acta["pasos"]["release_anterior"] = {
            "migraciones_aplicadas": len(previas),
            "ultima": previas[-1],
            "eventos_de_auditoria": antes,
            "segundos": round(time.perf_counter() - t, 1),
        }

        pitr._docker(
            "restart", ids["orig"]
        )  # el staging «duerme» y despierta: los datos están en el volumen, no en memoria
        pitr._esperar_listo(ids["orig"])
        puerto = pitr._puerto(ids["orig"])
        tras = int(pitr._psql(ids["orig"], "SELECT count(*) FROM audit.audit_events"))
        acta["pasos"]["base_persistente"] = {
            "eventos_antes_del_reinicio": antes,
            "eventos_tras_el_reinicio": tras,
            "ok": antes == tras and tras >= eventos,
        }

        # 2. backup previo
        t = time.perf_counter()
        pitr._docker(
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
        acta["pasos"]["backup_previo"] = {
            "segundos": round(time.perf_counter() - t, 1),
            "tamano_mb": int(
                pitr._docker(
                    "exec", ids["orig"], "sh", "-c", "du -sm /pitr/base/base1 | cut -f1"
                ).stdout.split()[0]
            ),
        }
        time.sleep(2)
        t0 = pitr._psql(
            ids["orig"],
            "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'",
        )
        time.sleep(2)

        # 3. migraciones como paso separado
        t_mig = time.perf_counter()
        segundos_paso = _migrar(puerto)
        acta["pasos"]["migraciones"] = {
            "segundos_del_paso": round(segundos_paso, 1),
            "reloj_pared_s": round(time.perf_counter() - t_mig, 1),
        }
        filas = pitr._psql(
            ids["orig"], "SELECT archivo, duracion_ms FROM core.schema_migrations ORDER BY archivo"
        ).splitlines()
        duraciones = {f.split("|")[0]: int(f.split("|")[1]) for f in filas}
        orden = pitr._psql(ids["orig"], "SELECT count(*) FROM core.schema_migrations")
        aplicadas_despues = int(
            pitr._psql(
                ids["orig"],
                f"SELECT count(*) FROM core.schema_migrations WHERE aplicado_en > '{t0}'",
            )
        )
        acta["presupuesto"] = evaluar_presupuesto(duraciones, segundos_paso)
        acta["pasos"]["migraciones"].update(
            {
                "en_el_ledger": int(orden),
                "las_del_artefacto": artefacto["migraciones"],
                "aplicadas_en_este_paso": aplicadas_despues,
                "ledger_coincide_con_el_artefacto": sorted(duraciones)
                == migraciones_del_artefacto(),
            }
        )

        # objetivo de recuperación declarado (KC-DB-11-1), tal y como está tras el despliegue (la tabla la crea la migración 061)
        obj = pitr._psql(
            ids["orig"],
            "SELECT rto_minutos, rpo_minutos, estado FROM core.objetivos_recuperacion ORDER BY (estado = 'aprobado') DESC, id DESC LIMIT 1",
        ).split("|")
        acta["objetivos"] = {
            "rto_minutos": int(obj[0]),
            "rpo_minutos": int(obj[1]),
            "estado": obj[2],
        }

        # 4. smoke de release
        variables = {
            "version_esperada": artefacto["version_esperada"],
            "ultima_migracion": artefacto["ultima_migracion"],
            "migraciones": artefacto["migraciones"],
        }
        admin = _smoke(ids["orig"], "kallicode_admin", "smoke_release.sql", variables)
        app = _smoke(
            ids["orig"],
            "kallicode_app",
            "smoke_post_deploy.sql",
            {
                "version_esperada": artefacto["version_esperada"],
                "ultima_migracion": artefacto["ultima_migracion"],
            },
        )
        negativo = _smoke(
            ids["orig"],
            "kallicode_admin",
            "smoke_release.sql",
            {**variables, "version_esperada": "v9.9.9"},
        )
        acta["smoke"] = {
            "admin_ok": admin["codigo"] == 0,
            "comprobaciones": _filas_smoke(admin["salida"]),
            "aplicacion_ok": app["codigo"] == 0,
            "control_negativo_falla_con_version_equivocada": negativo["codigo"] != 0,
        }
        acta["ok_smoke"] = bool(
            acta["smoke"]["admin_ok"]
            and acta["smoke"]["aplicacion_ok"]
            and acta["smoke"]["control_negativo_falla_con_version_equivocada"]
        )

        # actividad posterior al despliegue y cierre del segmento de WAL
        for i, tnt in enumerate(tenants):
            pitr._psql(
                ids["orig"],
                "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
                f"VALUES ('{tnt}', 'sistema', 'post_despliegue', 'evento {i}', repeat('0', 64), repeat('a', 64), '10.0.0.7', 'ga/1', 'req-ga-{i}-{run}')",
            )
        time.sleep(2)
        t1 = pitr._psql(
            ids["orig"],
            "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'",
        )
        eventos_t1 = int(pitr._psql(ids["orig"], "SELECT count(*) FROM audit.audit_events"))
        # Actividad POSTERIOR a t1, como en la vida real: sin una transacción posterior en el WAL, PostgreSQL aborta la
        # restauración («recovery ended before configured recovery target was reached»). Además demuestra que la
        # restauración es exacta: este evento NO debe aparecer en la instancia restaurada a t1.
        time.sleep(1.5)
        pitr._psql(
            ids["orig"],
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
            f"VALUES ('{tenants[0]}', 'sistema', 'posterior_a_t1', 'no debe estar tras restaurar a t1', repeat('0', 64), repeat('a', 64), '10.0.0.7', 'ga/1', 'req-ga-post-{run}')",
        )
        segmento = pitr._psql(ids["orig"], "SELECT pg_walfile_name(pg_switch_wal() - 1)")
        espera = time.perf_counter()
        while time.perf_counter() - espera < 180:
            if (
                pitr._psql(
                    ids["orig"], "SELECT coalesce(last_archived_wal, '') FROM pg_stat_archiver"
                )
                >= segmento
            ):
                break
            time.sleep(1)
        else:
            raise TimeoutError("el WAL posterior al despliegue no llegó a archivarse")

        # 5. pérdida del servidor y segundo ensayo de restauración
        pitr._docker("kill", ids["orig"])
        ra = pitr._restaurar(ids, "a", t1)
        ra["verificacion"] = pitr._verificar(ids["a"])
        ra["smoke"] = _smoke(ids["a"], "postgres", "smoke_release.sql", variables)["codigo"] == 0
        ra["eventos_restaurados"], ra["eventos_esperados"] = (
            int(pitr._psql(ids["a"], "SELECT count(*) FROM audit.audit_events")),
            eventos_t1,
        )
        ra["ultima_migracion"] = pitr._psql(
            ids["a"], "SELECT max(archivo) FROM core.schema_migrations"
        )
        rb = pitr._restaurar(ids, "b", t0)
        rb["verificacion"] = pitr._verificar(ids["b"])
        rb["ultima_migracion"] = pitr._psql(
            ids["b"], "SELECT max(archivo) FROM core.schema_migrations"
        )
        rb["eventos_restaurados"], rb["eventos_esperados"] = (
            int(pitr._psql(ids["b"], "SELECT count(*) FROM audit.audit_events")),
            antes,
        )
        objetivo_s = acta["objetivos"]["rto_minutos"] * 60
        ra["rto_dentro_del_objetivo"] = ra["rto_segundos"] <= objetivo_s
        rb["rto_dentro_del_objetivo"] = rb["rto_segundos"] <= objetivo_s
        acta["restauracion_posterior_al_despliegue"] = ra
        acta["restauracion_al_instante_previo"] = rb
        acta["rto_objetivo_s"] = objetivo_s
        acta["ok_restauracion"] = bool(
            ra["verificacion"]["ok"] and ra["smoke"] and ra["ultima_migracion"] == artefacto["ultima_migracion"] and ra["eventos_restaurados"] == eventos_t1
            and rb["verificacion"]["ok"] and rb["ultima_migracion"] == previas[-1] and rb["eventos_restaurados"] == antes
            and ra["rto_dentro_del_objetivo"] and rb["rto_dentro_del_objetivo"]
        )  # fmt: skip

        mig = acta["pasos"]["migraciones"]
        acta["ok_migraciones"] = bool(
            acta["presupuesto"]["dentro_del_presupuesto"] and mig["ledger_coincide_con_el_artefacto"] and mig["en_el_ledger"] == artefacto["migraciones"]
            and mig["aplicadas_en_este_paso"] == artefacto["migraciones"] - len(previas) and acta["pasos"]["base_persistente"]["ok"]
        )  # fmt: skip
        acta["duracion_total_s"] = round(time.perf_counter() - t_ensayo, 1)
        acta["cumple_criterios_tecnicos"] = bool(
            acta["ok_migraciones"] and acta["ok_smoke"] and acta["ok_restauracion"]
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not conservar:
            for c in (ids["orig"], ids["a"], ids["b"]):
                pitr._docker("rm", "-f", c, check=False)
            for v in (ids["arch"], ids["vol_orig"], ids["vol_a"], ids["vol_b"]):
                pitr._docker("volume", "rm", "-f", v, check=False)
    return acta


def markdown(a: dict[str, Any]) -> str:
    si = lambda b: "SÍ" if b else "NO"
    art, mig, pre, sm = a["artefacto"], a["pasos"]["migraciones"], a["presupuesto"], a["smoke"]
    ra, rb, obj = (
        a["restauracion_posterior_al_despliegue"],
        a["restauracion_al_instante_previo"],
        a["objetivos"],
    )
    firmas = "\n".join(
        f"| {rol} | {nombre} | _pendiente_ | _pendiente_ | _pendiente_ |"
        for rol, nombre in (
            ("Product Owner", "_pendiente_"),
            ("Architecture Board", "_pendiente_"),
            ("Tech Lead de Testing", "_pendiente_"),
            ("DBA: firma del despliegue del esquema", "Anderson González"),
        )
    )
    return f"""# Acta del ensayo de despliegue de GA (KC-DB-12-5)

Ejecutado: {a["ejecutado_en"]} · PostgreSQL {a["version_postgres"]} · duración total {a["duracion_total_s"]} s
Resultado técnico: **{"CUMPLE los criterios técnicos" if a["cumple_criterios_tecnicos"] else "NO CUMPLE los criterios técnicos"}** ·
Aceptación: **PENDIENTE DE FIRMAS** (este ensayo no firma ni aprueba nada)

## Criterio 1 · migraciones y smoke

| Comprobación | Resultado |
|---|---|
| Migraciones del artefacto aplicadas en orden sobre la base persistente | **{si(a["ok_migraciones"])}**: {mig["en_el_ledger"]} en el ledger de {mig["las_del_artefacto"]} que trae el artefacto; {mig["aplicadas_en_este_paso"]} en el paso de despliegue sobre una base ya en `{a["desde"]}` |
| Dentro del presupuesto | **{si(pre["dentro_del_presupuesto"])}**: suma {pre["suma_s"]} s (presupuesto {pre["presupuesto_total_s"]} s) · la más lenta, `{pre["mas_lenta"]}`, {pre["mas_lenta_s"]} s (presupuesto {pre["presupuesto_por_migracion_s"]} s por migración) · el paso de despliegue completo, {pre["paso_de_despliegue_s"]} s |
| Smoke de release (administrador) | **{si(sm["admin_ok"])}** |
| Smoke post-deploy (aplicación) | **{si(sm["aplicacion_ok"])}** |
| El smoke falla con una versión equivocada (control negativo) | **{si(sm["control_negativo_falla_con_version_equivocada"])}** |
| Base persistente: los datos sobreviven a un reinicio | **{si(a["pasos"]["base_persistente"]["ok"])}** ({a["pasos"]["base_persistente"]["eventos_antes_del_reinicio"]} → {a["pasos"]["base_persistente"]["eventos_tras_el_reinicio"]} eventos) |

Lo que verifica el smoke de release (versión `{art["version_esperada"]}`, última migración `{art["ultima_migracion"]}`, {art["migraciones"]} migraciones):

```
{chr(10).join(sm["comprobaciones"])}
```

El presupuesto es una **propuesta** (nadie lo había definido): unas veinte veces lo medido en Docker local. Hay que ratificarlo con el volumen del staging real.

## Criterio 2 · segundo ensayo de restauración PITR

Objetivo de RTO: {obj["rto_minutos"]} min ({obj["rto_minutos"] * 60} s), estado **{obj["estado"]}** en `core.objetivos_recuperacion`.

| | RTO medido | ¿Dentro del objetivo? | Cadena de auditoría y 26 chequeos | Estado restaurado |
|---|---|---|---|---|
| (a) al estado posterior al despliegue | {ra["rto_segundos"]} s | **{si(ra["rto_dentro_del_objetivo"])}** | {si(ra["verificacion"]["ok"])} ({ra["verificacion"]["tenants_con_cadena"]} tenants, {ra["verificacion"]["cadenas_rotas"]} cadenas rotas, {ra["verificacion"]["hallazgos"]} hallazgos) | `{ra["ultima_migracion"]}`, {ra["eventos_restaurados"]}/{ra["eventos_esperados"]} eventos; smoke de release {si(ra["smoke"])} |
| (b) al instante previo al despliegue (vuelta atrás) | {rb["rto_segundos"]} s | **{si(rb["rto_dentro_del_objetivo"])}** | {si(rb["verificacion"]["ok"])} ({rb["verificacion"]["tenants_con_cadena"]} tenants, {rb["verificacion"]["cadenas_rotas"]} cadenas rotas, {rb["verificacion"]["hallazgos"]} hallazgos) | `{rb["ultima_migracion"]}`, {rb["eventos_restaurados"]}/{rb["eventos_esperados"]} eventos |

El RTO cuenta desde que empieza el procedimiento de restauración hasta que la instancia nueva acepta conexiones fuera de recuperación
(no incluye detectar el desastre ni decidir restaurar ni la verificación posterior).

## Acta de aceptación

**Pendiente de firmas.** Las firmas son decisión de personas; esta acta recoge la evidencia para que decidan.

| Rol | Nombre | Decisión | Fecha | Firma |
|---|---|---|---|---|
{firmas}

## Límites de este ensayo

* **No es el staging real.** No hay un staging en el repositorio: es una simulación en Docker local con volúmenes persistentes y datos mínimos. La confirmación definitiva requiere repetirlo en el staging real con `docs/aceptacion_ga_esquema.md`.
* El RTO se mide contra un objetivo que sigue **{obj["estado"]}** (no aprobado por el Architecture Board).
* El presupuesto de migraciones es una propuesta; el del staging real dependerá de su volumen.
* «Release anterior» = el esquema hasta `{a["desde"]}`: es un punto de partida arbitrario para ensayar el despliegue sobre datos existentes.
* Los tiempos miden el procedimiento con un volumen mínimo, no una promesa para producción.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument(
        "--desde",
        default=DESDE_POR_DEFECTO,
        help="última migración de la «release anterior» (por defecto %(default)s)",
    )
    ap.add_argument("--eventos", type=int, default=3000)
    ap.add_argument("--conservar", action="store_true")
    args = ap.parse_args(argv)
    acta = ejecutar(args.desde, args.eventos, args.conservar)
    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "acta.json").write_text(
        json.dumps(acta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    texto = markdown(acta)
    (args.salida / "acta.md").write_text(texto, encoding="utf-8")
    print(texto)
    return 0 if acta["cumple_criterios_tecnicos"] else 1


if __name__ == "__main__":
    sys.exit(main())
