"""Orden y paginación unificada con conteo exacto y cursor (KC-BE-07-3, KC-BE-09-2)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError
from sqlalchemy.ext.asyncio import AsyncSession


def parse_sort(
    sort: str | None,
    allowlist: dict[str, str],
    default: tuple[str, str],
) -> tuple[str, bool]:
    """Devuelve (nombre_columna_sql, ascending) a partir del query param `sort`.

    `allowlist` mapea campo_api → nombre_columna_sql. El nombre devuelto es
    siempre uno de los valores de la allowlist, nunca el string del cliente.
    """
    if not sort:
        campo, direccion = default
        if campo not in allowlist:
            raise ValueError(f"El campo por defecto {campo!r} no está en la allowlist del listado.")
        return allowlist[campo], direccion == "asc"

    ascending = True
    campo = sort
    if campo.startswith("-"):
        ascending = False
        campo = campo[1:]

    if campo not in allowlist:
        raise AppError(
            "VALIDACION_ENTRADA",
            422,
            f"El campo de orden {campo!r} no está permitido en este listado.",
            {"campos_permitidos": sorted(allowlist)},
        )

    return allowlist[campo], ascending


async def paginar(
    db: AsyncSession,
    *,
    tabla: str,
    columnas: str,
    where: str,
    params: dict[str, Any],
    order_by: str,
    page: int = 1,
    page_size: int = 25,
    joins: str = "",
    group_by: str = "",
    limit: int | None = None,
    offset: int | None = None,
    consultar_todos: Any = todos,
    consultar_uno: Any = uno,
    conteo_primero: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Ejecuta la consulta paginada y el conteo total bajo la misma cláusula WHERE."""
    lim = limit if limit is not None else page_size
    off = offset if offset is not None else (page - 1) * page_size
    clausula_join = f" {joins.strip()}" if joins.strip() else ""
    clausula_group = f" GROUP BY {group_by.strip()}" if group_by.strip() else ""
    sql_items = f"""SELECT {columnas}
              FROM {tabla}{clausula_join}
             WHERE {where}{clausula_group}
             ORDER BY {order_by}
             LIMIT :_pag_lim OFFSET :_pag_off"""
    params_items = {**params, "_pag_lim": lim, "_pag_off": off}
    sql_total = f"SELECT count(*) AS n FROM {tabla} WHERE {where}"

    if conteo_primero:
        fila_total = await consultar_uno(db, sql_total, params)
        items = await consultar_todos(db, sql_items, params_items)
    else:
        items = await consultar_todos(db, sql_items, params_items)
        fila_total = await consultar_uno(db, sql_total, params)
    total = int(fila_total.get("n", fila_total.get("total", 0))) if fila_total else 0
    return items, total


def codificar_cursor(creado_en: datetime, numero: str) -> str:
    """Serializa la posición (creado_en, numero) en un cursor opaco URL-safe."""
    crudo = json.dumps(
        {"c": creado_en.isoformat(), "n": numero},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(crudo).decode("ascii").rstrip("=")


def decodificar_cursor(cursor: str) -> tuple[datetime, str]:
    """Valida y decodifica un cursor opaco emitido por el kanban."""
    if not cursor or len(cursor) > 256:
        raise AppError("VALIDACION_ENTRADA", 422, "El cursor de paginación no es válido.")
    relleno = "=" * (-len(cursor) % 4)
    try:
        datos = json.loads(base64.urlsafe_b64decode(cursor + relleno).decode("utf-8"))
        creado_en = datetime.fromisoformat(str(datos["c"]))
        numero = str(datos["n"])
        if not numero:
            raise ValueError("numero vacío")
    except Exception as exc:
        raise AppError(
            "VALIDACION_ENTRADA",
            422,
            "El cursor de paginación no es válido.",
        ) from exc
    return creado_en, numero
