"""ETag y If-Match para concurrencia optimista (KC-BE-07-3, §3.8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Response
from kallicode_core.errors import AppError


def calcular_etag(row: dict[str, Any]) -> str:
    marca: datetime | None = row.get("actualizado_en") or row.get("creado_en")
    if marca is None:
        raise ValueError(
            "La fila no tiene `actualizado_en` ni `creado_en`: no se puede calcular el ETag."
        )
    if marca.tzinfo is None:
        marca = marca.replace(tzinfo=UTC)
    ms = int(marca.timestamp() * 1000)
    return f'W/"{ms}"'


def verificar_if_match(if_match: str | None, etag_actual: str) -> None:
    if not if_match:
        return
    valores = [v.strip() for v in if_match.split(",") if v.strip()]
    if "*" in valores:
        return
    if etag_actual not in valores:
        raise AppError(
            "PRECONDICION_FALLIDA",
            412,
            "El recurso cambió desde que lo leíste. Vuelve a cargarlo y reintenta.",
        )


def poner_etag(response: Response, row: dict[str, Any]) -> str:
    etag = calcular_etag(row)
    response.headers["ETag"] = etag
    return etag
