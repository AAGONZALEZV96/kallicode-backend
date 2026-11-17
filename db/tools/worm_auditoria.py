#!/usr/bin/env python3
"""Export WORM de particiones de auditoría y verificación externa de no-alteración (KC-DB-10-2).

Una partición de ``audit.audit_events`` solo puede descartarse (``core.purgar_retencion``) si existe un comprobante
de export WORM verificado de su contenido actual (``audit.worm_exports``). Esta herramienta lo produce:

    exportar   serializa la partición a un archivo JSON Lines + un manifiesto, los verifica SIN base de datos y
               solo entonces registra el comprobante (``audit.registrar_export_worm``).
    verificar  comprueba un export ya hecho, offline: no usa la base de datos ni este repositorio, solo la
               biblioteca estándar. Es la «verificación externa de no-alteración».
    pendientes lista las particiones de auditoría y si tienen comprobante.
    comparar   DIAGNÓSTICO de una cadena rota (KC-DB-11-5): compara la partición en la base con su export y lista qué
               eventos y qué columnas difieren (alterados), faltan (borrados) o sobran (posteriores al export).
    restaurar  RESOLUCIÓN de una cadena rota (KC-DB-11-5, docs/runbook_incidentes_db.md §I3): devuelve un evento
               alterado a su contenido original, tomado de un export verificado, y comprueba que la cadena del
               tenant vuelve a ser íntegra. Exige rol superusuario (desactiva los disparadores solo en esa sentencia).

Formato (``kallicode-audit-worm/1``). Cada línea del archivo es un evento con todos los campos sellados, tal y como
los hasheó la base: ``datos`` es el texto de jsonb, ``ip`` la forma abreviada de inet y ``creado_en`` el texto
canónico (UTC, microsegundos). Así el sello se recalcula desde el propio archivo, sin reinterpretar nada.
El manifiesto guarda el sha256 del archivo, el digest del contenido, el resumen por tenant y el sha256 del export
anterior: los exports forman una cadena, como los eventos dentro de cada tenant.

El archivo se crea en modo exclusivo (nunca sobrescribe) y se deja de solo lectura. La inmutabilidad real
(«write once») la da el almacenamiento donde se deposite (volumen WORM, blob inmutable, object lock): esta herramienta
no puede garantizarla por sí sola.

Uso:
    python db/tools/worm_auditoria.py exportar --particion audit.audit_events_p20260101 --destino /ruta --por usuario
    python db/tools/worm_auditoria.py verificar /ruta/audit_events_p20260101.<fecha>.manifest.json
    python db/tools/worm_auditoria.py pendientes
Requiere KC_DATABASE_URL_MIGRATIONS (rol administrador) para exportar y listar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

FORMATO = "kallicode-audit-worm/1"
GENESIS = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")

CONSULTA_EVENTOS = (
    "SELECT id, tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos::text AS datos, "
    'modelo, step_id, sello_previo, sello, to_char(creado_en, \'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS creado_en, '
    "abbrev(ip) AS ip, user_agent, request_id, version_sello "
    "FROM {particion} ORDER BY tenant_id, id"
)


# --- canónico del sello (debe coincidir con audit.calcular_sello) ------------------------------------------
def sello_canonico(e: dict[str, Any]) -> str:
    base = (
        e["sello_previo"]
        + e["tenant_id"]
        + e["evento"]
        + e["resumen"]
        + (e["datos"] or "")
        + (e["ticket_id"] or "")
        + (e["actor_id"] or "")
    )
    if e["version_sello"] == 1:
        canonico = base + e["creado_en"]
    else:
        sep = "\x1f"
        canonico = (
            base
            + sep
            + (e["ip"] or "")
            + sep
            + (e["user_agent"] or "")
            + sep
            + (e["request_id"] or "")
            + sep
            + e["creado_en"]
        )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def digest_contenido(eventos: list[dict[str, Any]]) -> str:
    lineas = "\n".join(f"{e['id']}:{e['sello']}" for e in sorted(eventos, key=lambda x: x["id"]))
    return hashlib.sha256(lineas.encode("utf-8")).hexdigest()


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def resumen_tenants(eventos: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Resumen por tenant calculado SOLO con el archivo: eventos, primer sello_previo y último sello de la cadena."""
    errores: list[str] = []
    por_tenant: dict[str, list[dict[str, Any]]] = {}
    for e in eventos:
        por_tenant.setdefault(e["tenant_id"], []).append(e)
    out: dict[str, dict[str, Any]] = {}
    for tenant, evs in por_tenant.items():
        sellos = {e["sello"] for e in evs}
        siguientes: dict[str, dict[str, Any]] = {}
        for e in evs:
            if e["sello_previo"] in siguientes:
                errores.append(
                    f"tenant {tenant}: dos eventos comparten el eslabón anterior {e['sello_previo'][:12]}… (bifurcación)"
                )
            siguientes[e["sello_previo"]] = e
        inicios = [e for e in evs if e["sello_previo"] not in sellos]
        if len(inicios) != 1:
            errores.append(
                f"tenant {tenant}: la cadena tiene {len(inicios)} comienzos (se esperaba 1)"
            )
            continue
        actual = inicios[0]
        recorridos = 1
        while actual["sello"] in siguientes and recorridos <= len(evs):
            actual = siguientes[actual["sello"]]
            recorridos += 1
        if recorridos != len(evs):
            errores.append(
                f"tenant {tenant}: la cadena recorre {recorridos} de {len(evs)} eventos (hay eslabones sueltos)"
            )
        out[tenant] = {
            "eventos": len(evs),
            "primer_sello_previo": inicios[0]["sello_previo"],
            "ultimo_sello": actual["sello"],
        }
    return out, errores


# --- verificación externa (solo biblioteca estándar, sin base de datos) -----------------------------------
def verificar_archivo(
    manifiesto: Path, anterior: Path | None = None, manifiesto_sha256: str | None = None
) -> list[str]:
    """Devuelve la lista de problemas; vacía = el export está íntegro y no fue alterado."""
    errores: list[str] = []
    try:
        man = json.loads(manifiesto.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifiesto ilegible: {exc}"]
    if man.get("formato") != FORMATO:
        errores.append(f"formato desconocido: {man.get('formato')!r}")
    if manifiesto_sha256 is not None:
        real = hashlib.sha256(manifiesto.read_bytes()).hexdigest()
        if real != manifiesto_sha256:
            errores.append(
                f"el sha256 del manifiesto ({real[:12]}…) no es el registrado ({manifiesto_sha256[:12]}…)"
            )
    for campo in ("archivo_sha256", "contenido_digest", "export_anterior_sha256"):
        if not HEX64.match(str(man.get(campo, ""))):
            errores.append(f"manifiesto: {campo} no es un sha256 válido")
    archivo = manifiesto.parent / str(man.get("archivo", ""))
    if not archivo.is_file():
        return [*errores, f"falta el archivo exportado {archivo.name}"]

    # 1. el archivo no cambió
    real = sha256_archivo(archivo)
    if real != man.get("archivo_sha256"):
        errores.append(
            f"el sha256 del archivo ({real[:12]}…) no coincide con el del manifiesto ({str(man.get('archivo_sha256'))[:12]}…)"
        )

    # 2. cada línea es un evento y su sello se recalcula desde sus propios campos
    eventos: list[dict[str, Any]] = []
    for n, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            e = json.loads(linea)
        except ValueError:
            errores.append(f"línea {n}: no es JSON")
            continue
        try:
            if sello_canonico(e) != e["sello"]:
                errores.append(
                    f"línea {n} (evento {e.get('id')}): el sello no coincide con su contenido (evento alterado)"
                )
        except (KeyError, TypeError) as exc:
            errores.append(f"línea {n}: evento incompleto ({exc})")
            continue
        eventos.append(e)
    if len(eventos) != man.get("filas"):
        errores.append(
            f"el archivo tiene {len(eventos)} eventos y el manifiesto declara {man.get('filas')}"
        )
    if digest_contenido(eventos) != man.get("contenido_digest"):
        errores.append("el digest del contenido (id:sello) no coincide con el del manifiesto")

    # 3. el encadenamiento de cada tenant
    resumen, errs = resumen_tenants(eventos)
    errores.extend(errs)
    declarados = {t["tenant_id"]: t for t in man.get("tenants", [])}
    if set(declarados) != set(resumen):
        errores.append("los tenants del manifiesto no son los del archivo")
    for tenant, calc in resumen.items():
        dec = declarados.get(tenant)
        if dec is None:
            continue
        for campo in ("eventos", "primer_sello_previo", "ultimo_sello"):
            if dec.get(campo) != calc[campo]:
                errores.append(
                    f"tenant {tenant}: {campo} del manifiesto ({dec.get(campo)}) no es el del archivo ({calc[campo]})"
                )

    # 4. el enlace con el export anterior
    if anterior is not None:
        try:
            man_ant = json.loads(anterior.read_text(encoding="utf-8"))
            archivo_ant = anterior.parent / str(man_ant.get("archivo", ""))
            sha_ant = sha256_archivo(archivo_ant) if archivo_ant.is_file() else None
        except (OSError, ValueError) as exc:
            errores.append(f"export anterior ilegible: {exc}")
        else:
            if sha_ant != man.get("export_anterior_sha256"):
                errores.append("el export anterior no es el que enlaza este manifiesto")
            if sha_ant != man_ant.get("archivo_sha256"):
                errores.append("el archivo del export anterior fue alterado")
    return errores


# --- exportación ---------------------------------------------------------------------------------------------
def _dsn() -> str:
    url = os.environ.get("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        raise SystemExit("falta KC_DATABASE_URL_MIGRATIONS")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def exportar(conn: Any, particion: str, destino: Path, verificado_por: str) -> dict[str, Any]:
    """Exporta ``particion`` a ``destino``, lo verifica sin base de datos y registra el comprobante."""
    conn.execute("SET TIME ZONE 'UTC'")  # el canónico del sello usa la zona de la sesión
    fila = conn.execute(
        "SELECT (audit.limites_particion(%s::regclass)).desde", (particion,)
    ).fetchone()
    if fila is None or fila[0] is None:
        raise ValueError(f"{particion} no es una partición por rango de audit.audit_events")

    cur = conn.execute(CONSULTA_EVENTOS.format(particion=particion))
    columnas = [d.name for d in cur.description]
    eventos = [dict(zip(columnas, r, strict=True)) for r in cur.fetchall()]
    man_tenants = conn.execute(
        "SELECT audit.manifiesto_particion(%s::regclass)", (particion,)
    ).fetchone()[0]
    rotos = [t["tenant_id"] for t in man_tenants if not t["integra"]]
    if rotos:
        raise ValueError(
            f"cadena de auditoría no íntegra para {rotos}: no se exporta (audit.verify_chain)"
        )
    anterior = conn.execute(
        "SELECT archivo_sha256 FROM audit.worm_exports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    anterior_sha = anterior[0] if anterior else GENESIS

    destino.mkdir(parents=True, exist_ok=True)
    marca = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{particion.split('.')[-1]}.{marca}"
    ruta_archivo = destino / f"{base}.worm.jsonl"
    ruta_manifiesto = destino / f"{base}.manifest.json"
    with ruta_archivo.open(
        "x", encoding="utf-8", newline="\n"
    ) as f:  # modo exclusivo: nunca sobrescribe
        for e in eventos:
            f.write(json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    manifiesto = {
        "formato": FORMATO,
        "particion": particion,
        "filas": len(eventos),
        "archivo": ruta_archivo.name,
        "archivo_sha256": sha256_archivo(ruta_archivo),
        "contenido_digest": digest_contenido(eventos),
        "export_anterior_sha256": anterior_sha,
        "tenants": man_tenants,
        "generado_en": marca,
    }
    with ruta_manifiesto.open("x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifiesto, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    for ruta in (ruta_archivo, ruta_manifiesto):
        ruta.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    # verificación externa ANTES de registrar nada: si falla, no hay comprobante
    problemas = verificar_archivo(ruta_manifiesto)
    if problemas:
        raise ValueError("el export no supera la verificación externa: " + "; ".join(problemas))

    export_id = conn.execute(
        "SELECT audit.registrar_export_worm(%s::regclass, %s, %s, %s, %s, %s, %s::jsonb, %s)",
        (
            particion,
            manifiesto["contenido_digest"],
            manifiesto["archivo_sha256"],
            sha256_archivo(ruta_manifiesto),
            anterior_sha,
            str(ruta_archivo),
            json.dumps(man_tenants),
            verificado_por,
        ),
    ).fetchone()[0]
    return {
        "export_id": export_id,
        "archivo": ruta_archivo,
        "manifiesto": ruta_manifiesto,
        "archivo_sha256": manifiesto["archivo_sha256"],
        "filas": len(eventos),
    }


def comparar_con_export(conn: Any, manifiesto: Path) -> dict[str, Any]:
    """Compara la partición actual con su export WORM: eventos alterados (con sus columnas), borrados y añadidos.

    Los «añadidos» no son necesariamente una manipulación: una partición en curso sigue recibiendo eventos legítimos
    tras el export. Los alterados y los borrados sí lo son (la auditoría es append-only).
    """
    man = json.loads(manifiesto.read_text(encoding="utf-8"))
    export = {
        e["id"]: e
        for e in (
            json.loads(linea)
            for linea in (manifiesto.parent / man["archivo"])
            .read_text(encoding="utf-8")
            .splitlines()
        )
    }
    conn.execute("SET TIME ZONE 'UTC'")  # el canónico de creado_en usa la zona de la sesión
    cur = conn.execute(CONSULTA_EVENTOS.format(particion=man["particion"]))
    columnas = [d.name for d in cur.description]
    actual = {r[0]: dict(zip(columnas, r, strict=True)) for r in cur.fetchall()}
    alterados = [
        {"id": i, "columnas": sorted(c for c in export[i] if export[i][c] != actual[i].get(c))}
        for i in sorted(set(export) & set(actual))
        if export[i] != actual[i]
    ]
    return {
        "particion": man["particion"],
        "alterados": alterados,
        "borrados": sorted(set(export) - set(actual)),
        "anadidos": sorted(set(actual) - set(export)),
    }


def restaurar_evento(
    conn: Any, manifiesto: Path, evento_id: int, sha256_manifiesto: str | None = None
) -> dict[str, Any]:
    """Devuelve el evento ``evento_id`` a su contenido original, tomado de un export WORM verificado.

    Solo es legítimo si el export (a) supera la verificación externa y (b) es el registrado: pásese el
    sha256 del manifiesto de ``audit.worm_exports``. La corrección se hace con los disparadores desactivados
    (``session_replication_role = replica``, solo superusuario) únicamente para esa sentencia, y se COMPRUEBA que
    ``audit.verify_chain`` del tenant queda íntegra: si no, se deshace todo (``ROLLBACK``) y se lanza ValueError.
    El llamador confirma con ``conn.commit()``.
    """
    problemas = verificar_archivo(manifiesto, None, sha256_manifiesto)
    if problemas:
        raise ValueError("el export no es fiable, no se restaura nada: " + "; ".join(problemas))
    man = json.loads(manifiesto.read_text(encoding="utf-8"))
    original = None
    for linea in (manifiesto.parent / man["archivo"]).read_text(encoding="utf-8").splitlines():
        e = json.loads(linea)
        if e["id"] == evento_id:
            original = e
            break
    if original is None:
        raise ValueError(f"el evento {evento_id} no está en el export {manifiesto.name}")

    columnas = [c for c in original if c != "id"]
    sets = ", ".join(
        f"{c} = %s::jsonb"
        if c == "datos"
        else f"{c} = %s::timestamptz"
        if c == "creado_en"
        else f"{c} = %s"
        for c in columnas
    )
    valores = [original[c] for c in columnas]
    # ip es inet: el export guarda su forma abreviada (texto)
    sets = sets.replace("ip = %s", "ip = %s::inet")
    conn.execute("SET LOCAL session_replication_role = replica")
    try:
        cur = conn.execute(
            f"UPDATE audit.audit_events SET {sets} WHERE id = %s AND tenant_id = %s",
            [*valores, evento_id, original["tenant_id"]],
        )
        if cur.rowcount != 1:
            raise ValueError(f"se esperaba actualizar 1 evento y se actualizaron {cur.rowcount}")
    finally:
        conn.execute("SET LOCAL session_replication_role = origin")
    integra = conn.execute(
        "SELECT integra, primer_evento_roto FROM audit.verify_chain(%s)", (original["tenant_id"],)
    ).fetchone()
    if integra is None or not integra[0]:
        conn.rollback()
        raise ValueError(
            f"tras restaurar el evento {evento_id} la cadena de {original['tenant_id']} sigue rota "
            f"(primer evento roto: {None if integra is None else integra[1]}): el daño no era solo ese evento"
        )
    return {"evento": evento_id, "tenant": original["tenant_id"], "columnas_restauradas": columnas}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="orden", required=True)
    e = sub.add_parser("exportar")
    e.add_argument(
        "--particion",
        required=True,
        help="esquema.tabla de la partición, p. ej. audit.audit_events_p20260101",
    )
    e.add_argument("--destino", required=True, type=Path)
    e.add_argument("--por", required=True, help="quién verifica y registra el export")
    v = sub.add_parser("verificar")
    v.add_argument("manifiesto", type=Path)
    v.add_argument(
        "--anterior", type=Path, help="manifiesto del export anterior (verifica el enlace)"
    )
    v.add_argument("--sha256-manifiesto", help="sha256 registrado del manifiesto")
    sub.add_parser("pendientes")
    c_ = sub.add_parser(
        "comparar", help="compara la partición con su export (qué eventos difieren)"
    )
    c_.add_argument("manifiesto", type=Path)
    r_ = sub.add_parser("restaurar", help="restaura un evento alterado desde un export verificado")
    r_.add_argument("manifiesto", type=Path)
    r_.add_argument("--evento", required=True, type=int, help="id del evento alterado")
    r_.add_argument(
        "--sha256-manifiesto",
        required=True,
        help="sha256 REGISTRADO del manifiesto (audit.worm_exports)",
    )
    args = ap.parse_args(argv)

    if args.orden == "verificar":
        problemas = verificar_archivo(args.manifiesto, args.anterior, args.sha256_manifiesto)
        if problemas:
            print("EXPORT ALTERADO O INVÁLIDO:", file=sys.stderr)
            for p in problemas:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(
            "OK: el export está íntegro (archivo, sellos, cadenas y enlace verificados sin base de datos)"
        )
        return 0

    import psycopg  # solo exportar y pendientes necesitan la base

    with psycopg.connect(_dsn()) as conn:
        if args.orden == "pendientes":
            for p, desde, hasta, ex in conn.execute(
                "SELECT particion, desde, hasta, ultimo_export_id FROM audit.v_particiones_worm"
            ):
                print(
                    f"{p}\t{desde:%Y-%m-%d} .. {hasta:%Y-%m-%d}\t"
                    + (f"export nº {ex}" if ex else "SIN EXPORT")
                )
            return 0
        if args.orden == "comparar":
            d = comparar_con_export(conn, args.manifiesto)
            print(json.dumps(d, indent=2))
            return 1 if d["alterados"] or d["borrados"] else 0
        if args.orden == "restaurar":
            res = restaurar_evento(conn, args.manifiesto, args.evento, args.sha256_manifiesto)
            conn.commit()
            print(
                f"evento {res['evento']} restaurado; la cadena de {res['tenant']} vuelve a ser íntegra"
            )
            return 0
        r = exportar(conn, args.particion, args.destino, args.por)
        conn.commit()
        print(
            f"export nº {r['export_id']}: {r['filas']} eventos -> {r['archivo']} (sha256 {r['archivo_sha256']})"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
