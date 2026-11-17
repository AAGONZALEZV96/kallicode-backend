#!/usr/bin/env python3
"""Documentación del modelo de datos v2 generada desde pg_catalog (KC-DB-12-2).

Sustituye el mantenimiento manual del «Modelo de Base de Datos»: el documento sale del catálogo del sistema y de los
COMMENT del esquema, y la prueba ``packages/core/tests/integracion/test_documentacion_modelo.py`` lo compara en CI con
la copia versionada ``docs/modelo_datos_v2.md``.

Qué contiene el documento:
  * diccionario de datos por esquema y tabla (columnas con tipo, nulabilidad, valor por defecto, clave, valores
    permitidos y descripción), constraints, índices, triggers y políticas RLS;
  * mapa de relaciones: todas las claves foráneas, con la marca de cuáles son compuestas con ``tenant_id``, y un
    diagrama por esquema;
  * los chequeos de ``core.validar_integridad()``;
  * las afirmaciones corregidas de la auditoría (db/documentacion/afirmaciones_auditoria.json), cada una con una
    verificación SQL ejecutada contra el catálogo.

Alcance (el mismo que la instantánea de db/tools/congelar_esquema.py): esquemas core, vec, audit, cm y public; sin las
particiones hijas (las crea pg_partman con el tiempo) ni los objetos de extensiones. El documento es determinista: sin
OIDs, fechas ni datos de las tablas.

Uso:
  python db/tools/documentar_esquema.py                # regenera docs/modelo_datos_v2.md
  python db/tools/documentar_esquema.py --comprobar    # compara con la copia versionada; sale con 1 si difiere
  python db/tools/documentar_esquema.py --cobertura    # exige COMMENT en toda tabla, columna y constraint
  python db/tools/documentar_esquema.py --dsn postgresql://... --salida otro.md
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
DOCUMENTO = RAIZ / "docs" / "modelo_datos_v2.md"
AFIRMACIONES = RAIZ / "db" / "documentacion" / "afirmaciones_auditoria.json"

ESQUEMAS = ("core", "vec", "audit", "cm", "public")

TIPOS_CONSTRAINT = {
    "p": "clave primaria",
    "u": "única",
    "f": "clave foránea",
    "c": "regla (CHECK)",
    "x": "exclusión",
    "t": "disparador de constraint",
}
ACCIONES_FK = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}
COMANDOS_POLITICA = {"r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE", "*": "ALL"}

# Relaciones documentables: en los esquemas del alcance, sin partición hija ni objetos de extensión.
_FILTRO_REL = """
    n.nspname = ANY(%(esquemas)s)
    AND c.relkind IN ('r', 'p')
    AND NOT c.relispartition
    AND NOT EXISTS (SELECT 1 FROM pg_depend d
                     WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
"""

Modelo = dict[str, Any]


# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------
def _filas(conn: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(f) for f in conn.execute(sql, {"esquemas": list(ESQUEMAS), **params}).fetchall()]


def _valores_permitidos(definicion: str, columna: str) -> list[str] | None:
    """Valores de un CHECK ``columna = ANY (ARRAY[...])``; None si el CHECK es de otra forma."""
    m = re.search(rf"\b{re.escape(columna)}\b\s*=\s*ANY\s*\(\s*ARRAY\[(.*?)\]\s*\)", definicion)
    if not m:
        return None
    valores = re.findall(r"'((?:[^']|'')*)'::\w+|(?<![\w'])(-?\d+)(?![\w'])", m.group(1))
    return [(texto or numero).replace("''", "'") for texto, numero in valores] or None


def extraer(conn: Any) -> Modelo:
    tablas = _filas(
        conn,
        f"""
        SELECT c.oid::bigint AS oid, n.nspname AS esquema, c.relname AS tabla, c.relkind AS clase,
               obj_description(c.oid, 'pg_class') AS comentario,
               c.relrowsecurity AS rls, c.relforcerowsecurity AS rls_forzada,
               CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) END AS particion
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE {_FILTRO_REL}
         ORDER BY n.nspname, c.relname
        """,
    )
    por_oid = {t["oid"]: t for t in tablas}
    for t in tablas:
        t.update(columnas=[], constraints=[], indices=[], triggers=[], politicas=[])

    constraints = _filas(
        conn,
        f"""
        SELECT con.conrelid::bigint AS oid, con.oid::bigint AS coid, con.conname AS nombre, con.contype AS tipo,
               pg_get_constraintdef(con.oid) AS definicion,
               obj_description(con.oid, 'pg_constraint') AS comentario,
               con.conkey AS conkey, con.confkey AS confkey, con.confrelid::bigint AS destino_oid,
               con.confdeltype AS accion_borrado,
               ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ord)
                       JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = u.attnum
                      ORDER BY u.ord) AS columnas,
               ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY u(attnum, ord)
                       JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = u.attnum
                      ORDER BY u.ord) AS columnas_destino
          FROM pg_constraint con
          JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE {_FILTRO_REL} AND con.conparentid = 0 AND con.contype IN ('p','u','f','c','x','t')
         ORDER BY n.nspname, c.relname, con.conname
        """,
    )

    columnas = _filas(
        conn,
        f"""
        SELECT c.oid::bigint AS oid, a.attnum AS num, a.attname AS nombre,
               format_type(a.atttypid, a.atttypmod) AS tipo, a.attnotnull AS no_nula,
               pg_get_expr(ad.adbin, ad.adrelid) AS defecto, a.attidentity AS identidad,
               a.attgenerated AS generada, col_description(a.attrelid, a.attnum) AS comentario
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
          LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
         WHERE {_FILTRO_REL}
         ORDER BY n.nspname, c.relname, a.attnum
        """,
    )
    nombres_oid = {t["oid"]: f"{t['esquema']}.{t['tabla']}" for t in tablas}
    for k in constraints:
        k["destino"] = nombres_oid.get(k["destino_oid"]) if k["tipo"] == "f" else None
        k["compuesta_tenant"] = (
            k["tipo"] == "f" and len(k["columnas"]) > 1 and "tenant_id" in k["columnas"]
        )
        por_oid[k["oid"]]["constraints"].append(k)

    for col in columnas:
        t = por_oid[col["oid"]]
        col["clave"] = []
        col["valores"] = None
        for k in t["constraints"]:
            if col["nombre"] in k["columnas"]:
                if k["tipo"] == "p":
                    col["clave"].append("PK")
                elif k["tipo"] == "f":
                    col["clave"].append(f"FK → {k['destino']}")
                elif k["tipo"] == "c" and len(k["columnas"]) == 1:
                    col["valores"] = col["valores"] or _valores_permitidos(
                        k["definicion"], col["nombre"]
                    )
        t["columnas"].append(col)

    for i in _filas(
        conn,
        f"""
        SELECT c.oid::bigint AS oid, ic.relname AS nombre, pg_get_indexdef(ix.indexrelid) AS definicion,
               ix.indisunique AS unico, ix.indisprimary AS primario,
               obj_description(ix.indexrelid, 'pg_class') AS comentario
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_index ix ON ix.indrelid = c.oid JOIN pg_class ic ON ic.oid = ix.indexrelid
         WHERE {_FILTRO_REL}
         ORDER BY n.nspname, c.relname, ic.relname
        """,
    ):
        por_oid[i["oid"]]["indices"].append(i)

    for g in _filas(
        conn,
        f"""
        SELECT c.oid::bigint AS oid, t.tgname AS nombre, pg_get_triggerdef(t.oid) AS definicion,
               t.tgfoid::regproc::text AS funcion, t.tgconstraint <> 0 AS de_constraint
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_trigger t ON t.tgrelid = c.oid AND NOT t.tgisinternal AND t.tgparentid = 0
         WHERE {_FILTRO_REL}
         ORDER BY n.nspname, c.relname, t.tgname
        """,
    ):
        g["momento"] = _resumen_trigger(g["definicion"])
        por_oid[g["oid"]]["triggers"].append(g)

    for p in _filas(
        conn,
        f"""
        SELECT c.oid::bigint AS oid, pol.polname AS nombre, pol.polcmd AS comando, pol.polpermissive AS permisiva,
               COALESCE((SELECT string_agg(r.rolname, ', ' ORDER BY r.rolname) FROM pg_roles r
                          WHERE r.oid = ANY(pol.polroles)), 'PUBLIC') AS roles,
               pg_get_expr(pol.polqual, pol.polrelid) AS usando,
               pg_get_expr(pol.polwithcheck, pol.polrelid) AS con_check
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_policy pol ON pol.polrelid = c.oid
         WHERE {_FILTRO_REL}
         ORDER BY n.nspname, c.relname, pol.polname
        """,
    ):
        por_oid[p["oid"]]["politicas"].append(p)

    etiqueta = conn.execute("SELECT etiqueta FROM core.version_esquema()").fetchone()
    version = (
        (etiqueta["etiqueta"] if isinstance(etiqueta, dict) else etiqueta[0]) if etiqueta else None
    )
    chequeos = _filas(
        conn, "SELECT chequeo, descripcion FROM core.validar_integridad() ORDER BY chequeo"
    )
    afirmaciones = _verificar_afirmaciones(conn)
    return {
        "version": version,
        "tablas": tablas,
        "chequeos": chequeos,
        "afirmaciones": afirmaciones,
    }


def _resumen_trigger(definicion: str) -> str:
    m = re.search(
        r"CREATE (?:CONSTRAINT )?TRIGGER \S+ (BEFORE|AFTER|INSTEAD OF) (.*?) ON \S+", definicion
    )
    return f"{m.group(1)} {m.group(2)}" if m else definicion


def _verificar_afirmaciones(conn: Any) -> list[dict[str, Any]]:
    datos = json.loads(AFIRMACIONES.read_text(encoding="utf-8"))
    resultado: list[dict[str, Any]] = []
    for a in datos["afirmaciones"]:
        fila = conn.execute(a["verificacion"]).fetchone()
        valor = next(iter(fila.values())) if isinstance(fila, dict) else fila[0]
        resultado.append({**a, "verificada": bool(valor)})
    return resultado


# ---------------------------------------------------------------------------
# Cobertura de COMMENT
# ---------------------------------------------------------------------------
def faltantes(modelo: Modelo) -> list[str]:
    """Objetos sin COMMENT: toda tabla, columna y constraint debe llevarlo."""
    out: list[str] = []
    for t in modelo["tablas"]:
        nombre = f"{t['esquema']}.{t['tabla']}"
        if not (t["comentario"] or "").strip():
            out.append(f"tabla {nombre}")
        for c in t["columnas"]:
            if not (c["comentario"] or "").strip():
                out.append(f"columna {nombre}.{c['nombre']}")
        for k in t["constraints"]:
            if not (k["comentario"] or "").strip():
                out.append(f"constraint {nombre}.{k['nombre']}")
    return out


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------
def _c(texto: Any) -> str:
    """Texto seguro para una celda de tabla Markdown."""
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip().replace("|", "\\|")


def _codigo(texto: Any) -> str:
    t = _c(texto)
    return f"`{t.replace('`', chr(39))}`" if t else ""


def _id_mermaid(nombre: str) -> str:
    return nombre.replace(".", "_")


def _clase(t: Modelo) -> str:
    if t["clase"] == "p":
        return f"tabla particionada ({t['particion']})"
    return "tabla"


def _rls(t: Modelo) -> str:
    if t["rls"] and t["rls_forzada"]:
        return "RLS activa y forzada"
    if t["rls"]:
        return "RLS activa (no forzada)"
    return "sin RLS"


def _fks(modelo: Modelo) -> list[tuple[str, Modelo]]:
    return [
        (f"{t['esquema']}.{t['tabla']}", k)
        for t in modelo["tablas"]
        for k in t["constraints"]
        if k["tipo"] == "f"
    ]


def _resumen(modelo: Modelo) -> list[str]:
    filas: list[str] = []
    totales = [0] * 10
    for esquema in ESQUEMAS:
        ts = [t for t in modelo["tablas"] if t["esquema"] == esquema]
        if not ts:
            continue
        fks = [k for t in ts for k in t["constraints"] if k["tipo"] == "f"]
        valores = [
            len(ts),
            sum(t["clase"] == "p" for t in ts),
            sum(bool(t["rls"]) for t in ts),
            sum(len(t["columnas"]) for t in ts),
            sum(len(t["constraints"]) for t in ts),
            sum(len(t["indices"]) for t in ts),
            sum(len(t["triggers"]) for t in ts),
            sum(len(t["politicas"]) for t in ts),
            len(fks),
            sum(bool(k["compuesta_tenant"]) for k in fks),
        ]
        totales = [a + b for a, b in zip(totales, valores, strict=True)]
        filas.append(f"| `{esquema}` | " + " | ".join(str(v) for v in valores) + " |")
    filas.append("| **Total** | " + " | ".join(f"**{v}**" for v in totales) + " |")
    return [
        "| Esquema | Tablas | Particionadas | Con RLS | Columnas | Constraints | Índices | Triggers | Políticas | "
        "Claves foráneas | FK compuestas con `tenant_id` |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *filas,
    ]


def _mapa_relaciones(modelo: Modelo) -> list[str]:
    fks = _fks(modelo)
    out = [
        "## 3. Mapa de relaciones",
        "",
        f"{len(fks)} claves foráneas (sin las réplicas en particiones hijas). **Compuesta con `tenant_id`** = la clave "
        "foránea incluye `tenant_id`, de modo que una fila no puede referenciar a otra de un tenant distinto "
        "(migración 013_v21_fks_compuestas); las demás referencian por identificador global.",
        "",
        "| Tabla | Columnas | Referencia | Columnas referenciadas | Compuesta con `tenant_id` | Al borrar el padre |",
        "|---|---|---|---|---|---|",
    ]
    for origen, k in fks:
        out.append(
            f"| `{origen}` | {_codigo(', '.join(k['columnas']))} | `{k['destino']}` | "
            f"{_codigo(', '.join(k['columnas_destino']))} | {'**Sí**' if k['compuesta_tenant'] else 'No'} | "
            f"{ACCIONES_FK.get(k['accion_borrado'], k['accion_borrado'])} |"
        )
    out += [
        "",
        "Diagrama por esquema (relaciones dentro del esquema; flecha gruesa = compuesta con `tenant_id`; "
        "las relaciones entre esquemas están en la tabla de arriba).",
        "",
    ]
    for esquema in ESQUEMAS:
        propias = [
            (o, k)
            for o, k in fks
            if o.startswith(f"{esquema}.") and (k["destino"] or "").startswith(f"{esquema}.")
        ]
        if not propias:
            continue
        out += [f"### Esquema `{esquema}`", "", "```mermaid", "flowchart LR"]
        for o, k in propias:
            flecha = "==>" if k["compuesta_tenant"] else "-->"
            out.append(
                f"    {_id_mermaid(o)}[{o}] {flecha} {_id_mermaid(k['destino'])}[{k['destino']}]"
            )
        out += ["```", ""]
    return out


def _afirmaciones(modelo: Modelo) -> list[str]:
    out = [
        "## 5. Afirmaciones corregidas de la auditoría",
        "",
        "Cada afirmación del diseño original que la auditoría corrigió queda aquí con su corrección y con una "
        "verificación que se ejecuta contra el catálogo en cada generación: si deja de cumplirse, la prueba "
        "falla y obliga a revisar el esquema o la afirmación. Origen: `db/documentacion/afirmaciones_auditoria.json`.",
        "",
    ]
    for a in modelo["afirmaciones"]:
        out += [
            f"### {a['id']} · {a['tema']}",
            "",
            f"- **Afirmación original:** {a['afirmacion']}",
            f"- **Corrección:** {a['correccion']}",
            f"- **Fuente:** {a['fuente']}",
            f"- **Verificada contra el catálogo:** {'Sí' if a['verificada'] else '**NO**'}",
            "",
        ]
    return out


def _tabla(t: Modelo) -> list[str]:
    nombre = f"{t['esquema']}.{t['tabla']}"
    out = [
        f"#### `{nombre}`",
        "",
        _c(t["comentario"]),
        "",
        f"- **Tipo:** {_clase(t)} · **Seguridad:** {_rls(t)}",
        "",
        "| # | Columna | Tipo | Nulo | Por defecto | Clave | Descripción |",
        "|---:|---|---|---|---|---|---|",
    ]
    for c in t["columnas"]:
        por_defecto = c["defecto"]
        if c["identidad"]:
            por_defecto = "identidad" + (" (siempre)" if c["identidad"] == "a" else "")
        elif c["generada"]:
            por_defecto = f"generada: {c['defecto']}"
        desc = _c(c["comentario"])
        if c["valores"]:
            desc += f" **Valores permitidos:** {', '.join(f'`{v}`' for v in c['valores'])}."
        out.append(
            f"| {c['num']} | `{c['nombre']}` | {_codigo(c['tipo'])} | {'no' if c['no_nula'] else 'sí'} | "
            f"{_codigo(por_defecto)} | {_c(', '.join(c['clave']))} | {desc} |"
        )
    if t["constraints"]:
        out += [
            "",
            "**Constraints**",
            "",
            "| Nombre | Tipo | Definición | Descripción |",
            "|---|---|---|---|",
        ]
        for k in t["constraints"]:
            # Para un CHECK la descripción repite la definición: se muestra solo la definición.
            descripcion = "" if k["tipo"] == "c" else _c(k["comentario"])
            marca = " (compuesta con `tenant_id`)" if k["compuesta_tenant"] else ""
            out.append(
                f"| `{k['nombre']}` | {TIPOS_CONSTRAINT[k['tipo']]}{marca} | {_codigo(k['definicion'])} | {descripcion} |"
            )
    if t["indices"]:
        out += ["", "**Índices**", "", "| Nombre | Definición |", "|---|---|"]
        for i in t["indices"]:
            definicion = re.sub(r"^CREATE (UNIQUE )?INDEX \S+ ON ", r"\1ON ", i["definicion"])
            nota = f" — {_c(i['comentario'])}" if i["comentario"] else ""
            out.append(f"| `{i['nombre']}` | {_codigo(definicion)}{nota} |")
    if t["triggers"]:
        out += ["", "**Triggers**", "", "| Nombre | Cuándo | Función |", "|---|---|---|"]
        for g in t["triggers"]:
            out.append(f"| `{g['nombre']}` | {_c(g['momento'])} | `{g['funcion']}` |")
    if t["politicas"]:
        out += [
            "",
            "**Políticas RLS**",
            "",
            "| Nombre | Comando | Roles | USING | WITH CHECK |",
            "|---|---|---|---|---|",
        ]
        for p in t["politicas"]:
            out.append(
                f"| `{p['nombre']}` | {COMANDOS_POLITICA.get(p['comando'], p['comando'])} | {_c(p['roles'])} | "
                f"{_codigo(p['usando'])} | {_codigo(p['con_check'])} |"
            )
    out.append("")
    return out


def renderizar(modelo: Modelo) -> str:
    out: list[str] = [
        "# Modelo de datos v2 de KalliCode (generado)",
        "",
        "> **Documento generado. No se edita a mano.** Sale de `pg_catalog` y de los `COMMENT` del esquema con "
        "`python db/tools/documentar_esquema.py` (KC-DB-12-2). El CI lo regenera y falla si difiere de esta copia: "
        "para cambiarlo, cambia el esquema (migración con su `COMMENT`) o `db/documentacion/afirmaciones_auditoria.json` "
        "y vuelve a generarlo.",
        "",
        f"**Versión del esquema:** `{modelo['version']}` (`core.version_esquema()`) · "
        "Política de cambios: [politica-cambios-esquema.md](politica-cambios-esquema.md)",
        "",
        "## 1. Resumen",
        "",
        *_resumen(modelo),
        "",
        "## 2. Cómo se genera y se comprueba",
        "",
        "```bash",
        "uv run python db/tools/documentar_esquema.py              # regenera este documento",
        "uv run python db/tools/documentar_esquema.py --comprobar  # compara; sale con 1 si difiere",
        "uv run python db/tools/documentar_esquema.py --cobertura  # exige COMMENT en tablas, columnas y constraints",
        "```",
        "",
        "Alcance: esquemas `core`, `vec`, `audit`, `cm` y `public`, sin las particiones hijas (las crea `pg_partman`) ni "
        "los objetos de extensiones. Las descripciones son los `COMMENT` del esquema; los constraints tienen su "
        "descripción derivada de su definición en el catálogo (migración `068_comentarios_modelo.sql`).",
        "",
        *_mapa_relaciones(modelo),
        "## 4. Chequeos de integridad",
        "",
        f"`core.validar_integridad()` ejecuta {len(modelo['chequeos'])} chequeos de coherencia entre tablas que las "
        "claves foráneas no pueden expresar. Cada uno devuelve el número de filas con problema; lo esperado es cero.",
        "",
        "| Chequeo | Qué verifica |",
        "|---|---|",
        *[f"| `{c['chequeo']}` | {_c(c['descripcion'])} |" for c in modelo["chequeos"]],
        "",
        *_afirmaciones(modelo),
        "## 6. Diccionario de datos",
        "",
    ]
    for esquema in ESQUEMAS:
        ts = [t for t in modelo["tablas"] if t["esquema"] == esquema]
        if not ts:
            continue
        out += [f"### Esquema `{esquema}`", "", f"{len(ts)} tablas.", ""]
        for t in ts:
            out += _tabla(t)
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _dsn_por_defecto() -> str | None:
    url = os.getenv("KC_DATABASE_URL_ADMIN") or os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        return None
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def generar(dsn: str) -> tuple[Modelo, str]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        modelo = extraer(conn)
    return modelo, renderizar(modelo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dsn", default=_dsn_por_defecto(), help="por defecto KC_DATABASE_URL_ADMIN"
    )
    parser.add_argument("--salida", type=Path, default=DOCUMENTO)
    parser.add_argument("--comprobar", action="store_true", help="compara con --salida; no escribe")
    parser.add_argument(
        "--cobertura", action="store_true", help="solo exige COMMENT en todo; no escribe"
    )
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("indica --dsn o define KC_DATABASE_URL_ADMIN")

    modelo, texto = generar(args.dsn)

    if args.cobertura:
        sin = faltantes(modelo)
        if sin:
            print(f"Faltan COMMENT en {len(sin)} objetos:")
            print("\n".join(f"  - {s}" for s in sin[:200]))
            return 1
        print("Todas las tablas, columnas y constraints llevan COMMENT.")
        return 0

    erradas = [a["id"] for a in modelo["afirmaciones"] if not a["verificada"]]
    if erradas:
        print(f"Afirmaciones de la auditoría que el catálogo no cumple: {', '.join(erradas)}")
        return 1

    if args.comprobar:
        copia = args.salida.read_text(encoding="utf-8") if args.salida.exists() else ""
        if copia == texto:
            print(f"{args.salida} coincide con el catálogo.")
            return 0
        diff = difflib.unified_diff(
            copia.splitlines(), texto.splitlines(), "versionado", "generado", lineterm="", n=1
        )
        print(
            f"{args.salida} no coincide con el catálogo. Regenera con: python db/tools/documentar_esquema.py"
        )
        print("\n".join(list(diff)[:80]))
        return 1

    args.salida.write_text(texto, encoding="utf-8", newline="\n")
    print(f"Documento escrito en {args.salida} ({len(modelo['tablas'])} tablas).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
