"""Fase 3 - capa de DATOS: esquema de la base del cliente + uso desde codigo.

Dos fuentes deterministas: information_schema (tablas, columnas, FKs)
y el propio codigo (referencias a tablas en SQL embebido). El resultado
son los nodos Table/Column y las aristas USES_TABLE del grafo.
"""
import re

from sqlalchemy import create_engine, inspect

from config import settings


def extract_schema() -> tuple[list, list]:
    """Tablas, columnas y claves foraneas desde information_schema."""
    engine = create_engine(settings.client_db_url)
    insp = inspect(engine)
    tables, fks = [], []
    for table in insp.get_table_names():
        cols = [{"name": c["name"], "type": str(c["type"]),
                 "nullable": bool(c["nullable"])}
                for c in insp.get_columns(table)]
        tables.append({"name": table, "columns": cols})
        for fk in insp.get_foreign_keys(table):
            fks.append({
                "from_table": table,
                "from_cols": fk["constrained_columns"],
                "to_table": fk["referred_table"],
                "to_cols": fk["referred_columns"],
            })
    return tables, fks


# SQL embebido en el codigo (strings, query builders con SQL crudo)
SQL_STMT = re.compile(
    r"(?is)\b(select|insert\s+into|update|delete\s+from)\b[^;\"']{0,400}")
TABLE_REF = re.compile(r"(?i)\b(?:from|join|into|update)\s+[`\"]?(\w+)[`\"]?")


def find_table_usage(files, known_tables) -> list:
    """Detecta que archivos usan que tablas. Solo tablas confirmadas por
    el esquema: una coincidencia de regex que no existe en la base no
    entra al grafo (el grafo no adivina)."""
    known = {t["name"].lower() for t in known_tables}
    usage = []
    for sf in files:
        found = set()
        for stmt in SQL_STMT.finditer(sf.content):
            for ref in TABLE_REF.finditer(stmt.group(0)):
                if ref.group(1).lower() in known:
                    found.add(ref.group(1).lower())
        usage.extend({"file": sf.path, "table": t} for t in sorted(found))
    return usage
