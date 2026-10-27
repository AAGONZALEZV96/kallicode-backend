"""Fase 3 - capa de DATOS: esquema de la base del cliente + uso desde codigo.

Dos fuentes deterministas: information_schema (tablas, columnas, FKs)
y el propio codigo (referencias a tablas en SQL embebido). El resultado
son los nodos Table/Column y las aristas USES_TABLE del grafo.
"""

import re
from typing import Any

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import DBAPIError

from .config import Settings
from .ingest_repo import SourceFile
from .logging_setup import get_logger

logger = get_logger()

# SQLSTATE 57014 = query_canceled (lo que lanza Postgres al vencer statement_timeout)
_SQLSTATE_QUERY_CANCELED = "57014"


class SchemaExtractionTimeout(Exception):
    """Una consulta contra la base del cliente supero el tiempo limite (CM-F13)."""


def build_client_engine(settings: Settings) -> Engine:
    """Engine contra la base DEL CLIENTE con salvaguardas (CM-F13).

    Solo lectura a nivel de sesion (default_transaction_read_only), tiempo
    limite por sentencia y pool acotado: nunca mas de client_db_max_connections
    conexiones abiertas contra una base productiva ajena.
    """
    if settings.database_url is None:
        raise ValueError("database_url (base del cliente) no configurada")
    url = str(settings.database_url)
    max_conn = settings.client_db_max_connections
    options = (
        f"-c statement_timeout={settings.client_db_statement_timeout_ms} "
        "-c default_transaction_read_only=on"
    )
    return create_engine(
        url,
        pool_size=max_conn,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": options},
    )


def extract_schema(
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = build_client_engine(settings)
    logger.info(
        "extraccion de esquema del cliente: conexiones maximas acotadas",
        extra={
            "tenant_id": settings.tenant_id,
            "phase": "3/7",
            "max_connections": settings.client_db_max_connections,
            "statement_timeout_ms": settings.client_db_statement_timeout_ms,
        },
    )
    try:
        with engine.connect() as conn:
            return _read_schemas(inspect(conn), set(settings.client_db_exclude_schemas))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _SQLSTATE_QUERY_CANCELED:
            raise SchemaExtractionTimeout(
                f"la extraccion excedio {settings.client_db_statement_timeout_ms} ms"
            ) from exc
        raise
    finally:
        engine.dispose()


def _read_schemas(
    insp: Any, excluded: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    for schema in insp.get_schema_names():
        if schema in excluded:
            continue
        for table in insp.get_table_names(schema=schema):
            cols = [
                {"name": c["name"], "type": str(c["type"]), "nullable": bool(c["nullable"])}
                for c in insp.get_columns(table, schema=schema)
            ]
            tables.append({"name": table, "esquema": schema, "columns": cols})
            for fk in insp.get_foreign_keys(table, schema=schema):
                fks.append(
                    {
                        "from_table": table,
                        "from_schema": schema,
                        "from_cols": fk["constrained_columns"],
                        "to_table": fk["referred_table"],
                        "to_schema": fk.get("referred_schema") or schema,
                        "to_cols": fk["referred_columns"],
                    }
                )
    return tables, fks


# SQL embebido en el codigo (strings, query builders con SQL crudo)
SQL_STMT = re.compile(r"(?is)\b(select|insert\s+into|update|delete\s+from)\b[^;\"']{0,400}")
TABLE_REF = re.compile(r"(?i)\b(?:from|join|into|update)\s+[`\"]?(\w+)[`\"]?")


def find_table_usage(
    files: list[SourceFile], known_tables: list[dict[str, Any]]
) -> list[dict[str, str]]:
    known = {str(t["name"]).lower() for t in known_tables}
    usage: list[dict[str, str]] = []
    for sf in files:
        found = set()
        for stmt in SQL_STMT.finditer(sf.content):
            for ref in TABLE_REF.finditer(stmt.group(0)):
                if ref.group(1).lower() in known:
                    found.add(ref.group(1).lower())
        usage.extend({"file": sf.path, "table": t} for t in sorted(found))
    return usage
