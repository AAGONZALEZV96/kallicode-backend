"""Salud del servicio: /health (liveness) y /health/ready (dependencias)."""
from __future__ import annotations

from fastapi import APIRouter

from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, valor

router = APIRouter(tags=["Salud"])


@router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"estado": "ok"}


@router.get("/health/ready", summary="Readiness: postgres + redis")
async def ready() -> dict:
    """Salida: {postgres: bool, redis: bool}. 503 implícito si algo cae
    (el orquestador de contenedores decide por el body)."""
    salida = {"postgres": False, "redis": False}
    try:
        async with sesion_sistema() as db:
            salida["postgres"] = (await valor(db, "SELECT 1")) == 1
    except Exception:
        pass
    try:
        salida["redis"] = bool(await comercial.redis_cliente().ping())
    except Exception:
        pass
    return salida
