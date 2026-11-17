#!/usr/bin/env python3
"""Catálogo de nodos y aristas del grafo de CodeMapping generado desde el esquema cm (KC-CM-12-3).

El documento ``docs/catalogo_grafo_cm.md`` sale del catálogo de PostgreSQL ya migrado, no de texto escrito a
mano, para que no pueda desactualizarse. La prueba
``packages/core/tests/integracion/test_catalogo_grafo_cm.py`` y el CI (job ``database``) lo comparan con la copia
versionada.

Qué lee del esquema cm:
  * los tipos de nodo (``cm.node_types``) y la tabla que respalda cada uno, deducida de los triggers
    ``nodes_sync_ins`` / ``nodes_sync_del`` que mantienen ``cm.nodes`` (migración 022);
  * los tipos de arista (``cm.edge_types``) y los CHECK de ``cm.edges`` (``edges_tipo_chk``, ``edges_kind_chk``);
  * los subtipos de símbolo (``symbols_kind_chk``);
  * las columnas y los COMMENT de cada tabla de nodo y de ``cm.edges``;
  * las relaciones con nodos que viven fuera de ``cm.edges`` (claves foráneas hacia ``cm.nodes``).

Además comprueba que esas fuentes digan lo mismo: un tipo de nodo sin tabla ni triggers, un tipo de arista que el
CHECK no admite o un kind del CHECK que no está en el catálogo hacen fallar la generación.

Uso:
  python db/tools/catalogo_grafo_cm.py                # regenera docs/catalogo_grafo_cm.md
  python db/tools/catalogo_grafo_cm.py --comprobar    # compara con la copia versionada; sale con 1 si difiere
  python db/tools/catalogo_grafo_cm.py --dsn postgresql://... --salida otro.md
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
DOCUMENTO = RAIZ / "docs" / "catalogo_grafo_cm.md"
REGENERAR = "python db/tools/catalogo_grafo_cm.py"

Modelo = dict[str, Any]


# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------
def valores_permitidos(definicion: str, columna: str) -> list[str]:
    """Valores de un CHECK ``columna = ANY (ARRAY['a'::text, ...])``; lista vacía si no tiene esa forma."""
    m = re.search(rf"\b{re.escape(columna)}\b\s*=\s*ANY\s*\(\s*ARRAY\[(.*?)\]\s*\)", definicion)
    if not m:
        return []
    return [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'::\w+", m.group(1))]


def _filas(conn: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(f) for f in conn.execute(sql, params).fetchall()]


def _check(conn: Any, tabla: str, nombre: str) -> str:
    filas = _filas(
        conn,
        """
        SELECT pg_get_constraintdef(oid) AS definicion
          FROM pg_constraint
         WHERE conrelid = %(tabla)s::regclass AND conname = %(nombre)s AND contype = 'c'
        """,
        tabla=tabla,
        nombre=nombre,
    )
    return str(filas[0]["definicion"]) if filas else ""


def _columnas(conn: Any, tabla: str) -> list[dict[str, Any]]:
    return _filas(
        conn,
        """
        SELECT a.attname AS nombre,
               format_type(a.atttypid, a.atttypmod) AS tipo,
               a.attnotnull AS obligatoria,
               COALESCE(col_description(a.attrelid, a.attnum), '') AS descripcion
          FROM pg_attribute a
         WHERE a.attrelid = %(tabla)s::regclass AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
        """,
        tabla=tabla,
    )


def _comentario_tabla(conn: Any, tabla: str) -> str:
    filas = _filas(
        conn,
        "SELECT COALESCE(obj_description(%(tabla)s::regclass, 'pg_class'), '') AS c",
        tabla=tabla,
    )
    return str(filas[0]["c"])


def extraer(conn: Any) -> Modelo:
    tipos_nodo = _filas(
        conn, "SELECT kind, categoria, descripcion FROM cm.node_types ORDER BY categoria, kind"
    )
    tipos_arista = _filas(
        conn, "SELECT tipo, es_dirigida, descripcion FROM cm.edge_types ORDER BY tipo"
    )

    # Tabla que respalda cada kind: el argumento de los triggers que sincronizan cm.nodes.
    triggers = _filas(
        conn,
        """
        SELECT c.relname AS tabla, t.tgname AS trigger, t.tgargs AS args
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'cm' AND NOT t.tgisinternal
           AND t.tgname IN ('nodes_sync_ins', 'nodes_sync_del')
         ORDER BY c.relname, t.tgname
        """,
    )
    respaldo: dict[str, dict[str, Any]] = {}
    for t in triggers:
        # tgargs es bytea: los argumentos van separados (y terminados) por un byte nulo.
        kind = bytes(t["args"]).split(b"\x00", 1)[0].decode("utf-8")
        entrada = respaldo.setdefault(kind, {"tablas": set(), "triggers": set()})
        entrada["tablas"].add(f"cm.{t['tabla']}")
        entrada["triggers"].add(str(t["trigger"]))

    tablas_nodo: dict[str, Modelo] = {}
    for kind in sorted(respaldo):
        for tabla in sorted(respaldo[kind]["tablas"]):
            tablas_nodo[tabla] = {
                "comentario": _comentario_tabla(conn, tabla),
                "columnas": _columnas(conn, tabla),
            }

    tipo_chk = _check(conn, "cm.edges", "edges_tipo_chk")
    kind_chk = _check(conn, "cm.edges", "edges_kind_chk")
    simbolo_chk = _check(conn, "cm.symbols", "symbols_kind_chk")

    relaciones_externas = _filas(
        conn,
        """
        SELECT c.conrelid::regclass::text AS tabla, c.conname AS constraint,
               pg_get_constraintdef(c.oid) AS definicion,
               COALESCE(obj_description(c.conrelid, 'pg_class'), '') AS comentario
          FROM pg_constraint c
          JOIN pg_class r ON r.oid = c.conrelid
         WHERE c.contype = 'f' AND c.confrelid = 'cm.nodes'::regclass
           AND NOT r.relispartition
         ORDER BY 1, 2
        """,
    )

    return {
        "tipos_nodo": tipos_nodo,
        "tipos_arista": tipos_arista,
        "respaldo": {
            k: {"tablas": sorted(v["tablas"]), "triggers": sorted(v["triggers"])}
            for k, v in respaldo.items()
        },
        "tablas_nodo": tablas_nodo,
        "arista": {
            "comentario": _comentario_tabla(conn, "cm.edges"),
            "columnas": _columnas(conn, "cm.edges"),
            "check_tipo": valores_permitidos(tipo_chk, "tipo"),
            "check_src": valores_permitidos(kind_chk, "src_kind"),
            "check_dst": valores_permitidos(kind_chk, "dst_kind"),
        },
        "subtipos_simbolo": valores_permitidos(simbolo_chk, "kind"),
        "relaciones_externas": relaciones_externas,
    }


# ---------------------------------------------------------------------------
# Coherencia entre fuentes
# ---------------------------------------------------------------------------
def incoherencias(modelo: Modelo) -> list[str]:
    """Contradicciones entre el catálogo, los CHECK y los triggers. Vacía si todo cuadra."""
    errores: list[str] = []
    kinds = {str(n["kind"]) for n in modelo["tipos_nodo"]}
    tipos = {str(a["tipo"]) for a in modelo["tipos_arista"]}
    arista = modelo["arista"]

    def comparar(nombre: str, esperado: set[str], obtenido: set[str]) -> None:
        for falta in sorted(esperado - obtenido):
            errores.append(f"{nombre}: falta '{falta}'")
        for sobra in sorted(obtenido - esperado):
            errores.append(f"{nombre}: '{sobra}' no está en el catálogo")

    comparar("cm.edges.edges_tipo_chk frente a cm.edge_types", tipos, set(arista["check_tipo"]))
    comparar(
        "cm.edges.edges_kind_chk (src_kind) frente a cm.node_types", kinds, set(arista["check_src"])
    )
    comparar(
        "cm.edges.edges_kind_chk (dst_kind) frente a cm.node_types", kinds, set(arista["check_dst"])
    )
    comparar("triggers nodes_sync_* frente a cm.node_types", kinds, set(modelo["respaldo"]))

    for kind, r in sorted(modelo["respaldo"].items()):
        if len(r["tablas"]) != 1:
            errores.append(f"kind '{kind}': lo respaldan {len(r['tablas'])} tablas ({r['tablas']})")
        if r["triggers"] != ["nodes_sync_del", "nodes_sync_ins"]:
            errores.append(
                f"kind '{kind}': le falta un trigger de alta o de baja ({r['triggers']})"
            )
    return errores


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def _c(texto: Any) -> str:
    """Texto seguro dentro de una celda de tabla Markdown."""
    return str(texto).replace("|", "\\|").replace("\n", " ").strip()


def _columnas_md(columnas: list[dict[str, Any]]) -> list[str]:
    out = ["| Columna | Tipo | Obligatoria | Descripción |", "|---|---|---|---|"]
    for col in columnas:
        out.append(
            f"| `{col['nombre']}` | `{col['tipo']}` | {'sí' if col['obligatoria'] else 'no'} "
            f"| {_c(col['descripcion'])} |"
        )
    return out


def renderizar(modelo: Modelo) -> str:
    arista = modelo["arista"]
    tipos_nodo = modelo["tipos_nodo"]
    tipos_arista = modelo["tipos_arista"]
    out: list[str] = [
        "# Catálogo de nodos y aristas del grafo de CodeMapping",
        "",
        "> **Documento generado. No lo edites a mano.** Sale del esquema `cm` ya migrado con",
        f"> `{REGENERAR}` (KC-CM-12-3) y el CI lo compara con esta copia en cada PR:",
        "> si añades un tipo de nodo o de arista en una migración, regenera el documento en el mismo PR.",
        "",
        "Fuentes: `cm.node_types`, `cm.edge_types`, los CHECK `edges_tipo_chk` y `edges_kind_chk` de",
        "`cm.edges`, `symbols_kind_chk` de `cm.symbols`, los triggers `nodes_sync_ins` / `nodes_sync_del`",
        "que mantienen `cm.nodes` y los COMMENT del esquema. La generación falla si esas fuentes se",
        "contradicen.",
        "",
        "## 1. Resumen",
        "",
        f"- **{len(tipos_nodo)} tipos de nodo** en "
        f"{len({n['categoria'] for n in tipos_nodo})} categorías.",
        f"- **{len(tipos_arista)} tipos de arista**, "
        f"{sum(1 for a in tipos_arista if a['es_dirigida'])} dirigidos.",
        "- Todo nodo tiene una fila en `cm.nodes (tenant_id, id, kind)`; el id es único por tenant sea cual",
        "  sea su kind. Toda arista es una fila de `cm.edges (tenant_id, tipo, src_id, dst_id)`.",
        "- Aislamiento: todas las tablas llevan `tenant_id` y RLS por tenant.",
        "",
        "## 2. Tipos de nodo",
        "",
        "| Kind | Categoría | Tabla que lo respalda | Descripción |",
        "|---|---|---|---|",
    ]
    for n in tipos_nodo:
        tablas = modelo["respaldo"].get(n["kind"], {}).get("tablas", [])
        respaldo = ", ".join(f"`{t}`" for t in tablas) or "—"
        out.append(
            f"| `{n['kind']}` | {_c(n['categoria'])} | {respaldo} | {_c(n['descripcion'])} |"
        )
    if modelo["subtipos_simbolo"]:
        out += [
            "",
            "Subtipos de `symbol` (columna `cm.symbols.kind`, CHECK `symbols_kind_chk`): "
            + ", ".join(f"`{s}`" for s in modelo["subtipos_simbolo"])
            + ".",
        ]

    out += [
        "",
        "## 3. Tipos de arista",
        "",
        "| Tipo | Dirigida | Descripción |",
        "|---|---|---|",
    ]
    for a in tipos_arista:
        out.append(
            f"| `{a['tipo']}` | {'sí' if a['es_dirigida'] else 'no'} | {_c(a['descripcion'])} |"
        )
    out += [
        "",
        "Extremos admitidos por `edges_kind_chk`:",
        "",
        "- `src_kind`: " + ", ".join(f"`{k}`" for k in arista["check_src"]) + ".",
        "- `dst_kind`: " + ", ".join(f"`{k}`" for k in arista["check_dst"]) + ".",
        "",
        "> El esquema no restringe qué pares de kinds admite cada tipo de arista: cualquier tipo puede unir",
        "> cualquier par de los de arriba (la vista `cm.v_catalogo_grafo` también los da todos por válidos).",
        "> Qué pares produce de verdad cada fase del pipeline está en",
        "> [packages/codemapping/README.md](../packages/codemapping/README.md).",
        "",
        "### Columnas de `cm.edges`",
        "",
        _c(arista["comentario"]),
        "",
        *_columnas_md(arista["columnas"]),
        "",
        "## 4. Relaciones con nodos fuera de `cm.edges`",
        "",
        "Tablas que apuntan a un nodo por clave foránea a `cm.nodes` (la acción al borrar el nodo",
        "está en la definición).",
        "",
        "| Tabla | Constraint | Definición | Qué guarda |",
        "|---|---|---|---|",
    ]
    for r in modelo["relaciones_externas"]:
        out.append(
            f"| `{r['tabla']}` | `{r['constraint']}` | `{_c(r['definicion'])}` | {_c(r['comentario'])} |"
        )

    out += ["", "## 5. Tablas de nodo", ""]
    for tabla, datos in modelo["tablas_nodo"].items():
        kind = next(k for k, r in modelo["respaldo"].items() if tabla in r["tablas"])
        out += [f"### `{tabla}` — kind `{kind}`", "", _c(datos["comentario"]), ""]
        out += _columnas_md(datos["columnas"])
        out.append("")
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
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("indica --dsn o define KC_DATABASE_URL_ADMIN")

    modelo, texto = generar(args.dsn)

    errores = incoherencias(modelo)
    if errores:
        print("El esquema cm se contradice:")
        print("\n".join(f"  - {e}" for e in errores))
        return 1

    if args.comprobar:
        copia = args.salida.read_text(encoding="utf-8") if args.salida.exists() else ""
        if copia == texto:
            print(f"{args.salida} coincide con el esquema cm.")
            return 0
        diff = difflib.unified_diff(
            copia.splitlines(), texto.splitlines(), "versionado", "generado", lineterm="", n=1
        )
        print(f"{args.salida} no coincide con el esquema cm. Regenera con: {REGENERAR}")
        print("\n".join(list(diff)[:80]))
        return 1

    args.salida.write_text(texto, encoding="utf-8", newline="\n")
    print(
        f"Catálogo escrito en {args.salida} ({len(modelo['tipos_nodo'])} tipos de nodo, "
        f"{len(modelo['tipos_arista'])} de arista)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
