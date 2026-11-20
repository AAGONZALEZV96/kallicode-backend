"""Orden seguro para listados paginados (KC-BE-07-3, §3.4)."""

from __future__ import annotations

from kallicode_core.errors import AppError


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
