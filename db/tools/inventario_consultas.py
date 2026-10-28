"""Inventario PROVISIONAL de consultas SQL por endpoint (KC-DB-06-1).

El inventario definitivo lo entrega Backend (KC-BE-06-1). Mientras no exista, este script
lo aproxima leyendo el código: recorre los routers de la API y los módulos de servicio,
y para cada función extrae los literales SQL (SELECT, INSERT, UPDATE, DELETE, WITH), con las
tablas que tocan y las columnas por las que filtran, unen u ordenan.

Es una aproximación estática: no ve el SQL que se arma dinámicamente más allá de los
fragmentos literales de un f-string, ni qué consultas son realmente las más ejecutadas.
Cuando Backend publique su inventario, reemplaza la sección «Inventario» de
docs/plan-indices.md y este script deja de ser necesario.

Uso:
    python db/tools/inventario_consultas.py            # tabla en Markdown
    python db/tools/inventario_consultas.py --json     # una lista JSON de consultas
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

ORIGENES = (
    ("api", RAIZ / "apps" / "api" / "src" / "kallicode_api" / "routers"),
    ("api", RAIZ / "apps" / "api" / "src" / "kallicode_api"),
    ("core", RAIZ / "packages" / "core" / "src" / "kallicode_core"),
    ("worker", RAIZ / "apps" / "workers" / "src" / "kallicode_workers"),
    ("codemapping", RAIZ / "packages" / "codemapping" / "src" / "kallicode_codemapping"),
)

_METODOS = {"get", "post", "put", "patch", "delete"}
_SQL = re.compile(r"^\s*(select|with|insert\s+into|update|delete\s+from)\b", re.IGNORECASE)
_TABLA = re.compile(
    r"\b(?:from|join|into|update)\s+((?:core|vec|audit|cm)\.[a-z_][a-z0-9_]*)", re.IGNORECASE
)
_COLUMNA = re.compile(
    r"\b(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*(?:=|<>|!=|<=|>=|<|>|<=>|@>|&&|\bin\b|\blike\b|\bis\b|\bilike\b)",
    re.IGNORECASE,
)
_PALABRAS = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "not",
    "null",
    "true",
    "false",
    "set",
    "values",
    "on",
    "as",
    "case",
    "when",
    "then",
    "else",
    "end",
    "by",
    "limit",
    "offset",
    "in",
    "is",
    "like",
    "ilike",
    "exists",
    "any",
    "all",
    "now",
    "interval",
    "cast",
    "returning",
}


@dataclass
class Consulta:
    id: str = ""
    origen: str = ""
    endpoint: str = ""
    funcion: str = ""
    archivo: str = ""
    linea: int = 0
    tipo: str = ""
    tablas: list[str] = field(default_factory=list)
    filtros: list[str] = field(default_factory=list)
    orden: list[str] = field(default_factory=list)
    sql: str = ""


def _texto_sql(nodo: ast.AST) -> str | None:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else " {} "
            for v in nodo.values
        )
    return None


def _columnas(fragmento: str) -> list[str]:
    vistas: list[str] = []
    for m in _COLUMNA.finditer(fragmento):
        col = m.group(1).lower()
        if col not in _PALABRAS and col not in vistas and not col.isdigit():
            vistas.append(col)
    return vistas


def _analizar(sql: str) -> tuple[str, list[str], list[str], list[str]]:
    plano = re.sub(r"\s+", " ", sql).strip()
    tipo = re.match(r"(?i)(select|with|insert|update|delete)", plano)
    tablas = sorted({t.lower() for t in _TABLA.findall(plano)})
    filtros: list[str] = []
    for tramo in re.findall(
        r"(?i)\b(?:where|on|having)\b(.*?)(?=\bgroup\b|\border\b|\blimit\b|\breturning\b|\bunion\b|$)",
        plano,
    ):
        for c in _columnas(tramo):
            if c not in filtros:
                filtros.append(c)
    orden: list[str] = []
    for tramo in re.findall(r"(?i)\border\s+by\b(.*?)(?=\blimit\b|\boffset\b|\)|$)", plano):
        for token in re.findall(r"(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)", tramo, re.IGNORECASE):
            if token.lower() not in _PALABRAS | {"asc", "desc", "nulls", "first", "last"}:
                if token.lower() not in orden:
                    orden.append(token.lower())
    return (tipo.group(1).upper() if tipo else "?"), tablas, filtros, orden


def _prefijo_router(arbol: ast.Module) -> str:
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and getattr(nodo.func, "id", getattr(nodo.func, "attr", "")) == "APIRouter"
        ):
            for kw in nodo.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value)
    return ""


def _endpoint(fn: ast.AsyncFunctionDef | ast.FunctionDef, prefijo: str) -> str:
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _METODOS
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return f"{dec.func.attr.upper()} {prefijo}{dec.args[0].value}"
    return ""


def extraer() -> list[Consulta]:
    consultas: list[Consulta] = []
    vistos: set[Path] = set()
    for origen, carpeta in ORIGENES:
        for ruta in sorted(carpeta.glob("*.py")):
            if ruta in vistos or ruta.name == "__init__.py":
                continue
            vistos.add(ruta)
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            prefijo = _prefijo_router(arbol)
            for fn in ast.walk(arbol):
                if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                    continue
                endpoint = _endpoint(fn, prefijo)
                for nodo in ast.walk(fn):
                    texto = _texto_sql(nodo)
                    if texto is None or not _SQL.match(texto):
                        continue
                    tipo, tablas, filtros, orden = _analizar(texto)
                    if not tablas:
                        continue
                    consultas.append(
                        Consulta(
                            origen=origen,
                            endpoint=endpoint,
                            funcion=fn.name,
                            archivo=str(ruta.relative_to(RAIZ)).replace("\\", "/"),
                            linea=nodo.lineno,
                            tipo=tipo,
                            tablas=tablas,
                            filtros=filtros,
                            orden=orden,
                            sql=re.sub(r"\s+", " ", texto).strip(),
                        )
                    )
    # Una misma sentencia puede aparecer por funciones anidadas: se deja la de la función
    # más interna (la que la contiene directamente) y se numera en orden estable.
    unicas: dict[tuple[str, int], Consulta] = {}
    for c in consultas:
        clave = (c.archivo, c.linea)
        previa = unicas.get(clave)
        if (
            previa is None
            or (not previa.endpoint and c.endpoint)
            or (bool(previa.endpoint) == bool(c.endpoint))
        ):
            unicas[clave] = c
    ordenadas = sorted(unicas.values(), key=lambda c: (c.archivo, c.linea))
    for i, c in enumerate(ordenadas, start=1):
        c.id = f"Q-{i:03d}"
    return ordenadas


def _markdown(consultas: list[Consulta]) -> str:
    filas = [
        "| Id | Endpoint o función | Tipo | Tablas | Filtra / une por | Ordena por | Origen |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in consultas:
        donde = c.endpoint or f"({c.origen}) `{c.funcion}`"
        filas.append(
            f"| {c.id} | {donde} | {c.tipo} | {', '.join(f'`{t}`' for t in c.tablas)} | "
            f"{', '.join(c.filtros) or '-'} | {', '.join(c.orden) or '-'} | "
            f"`{c.archivo}:{c.linea}` |"
        )
    return "\n".join(filas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="salida JSON en vez de Markdown")
    args = ap.parse_args()
    consultas = extraer()
    if args.json:
        print(json.dumps([asdict(c) for c in consultas], ensure_ascii=False, indent=1))
    else:
        print(_markdown(consultas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
