"""Consumo (§15.1–15.2 del diseño): resumen del ciclo y análisis por ticket."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query

from kallicode_core.comercial import ciclo_actual, estado_cuota_loc, plan_vigente
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError

from ..deps import UsuarioActual, sesion_de, usuario_actual

router = APIRouter(prefix="/usage", tags=["Consumo y plan"])


def _validar_ciclo(ciclo: str | None) -> str:
    c = ciclo or ciclo_actual()
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", c) or c > ciclo_actual():
        raise AppError("CICLO_INVALIDO", 422, "El ciclo indicado no existe.")
    return c


@router.get("/summary", summary="Resumen de consumo del ciclo")
async def summary(ciclo: str | None = Query(default=None),
                  usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: plan, loc {consumidas, restantes, porcentaje}, tickets, umbral.
    Es la vista de la validación comercial QU-1: los mismos números que el
    middleware."""
    c = _validar_ciclo(ciclo)
    async with sesion_de(usuario) as db:
        plan = await plan_vigente(db, usuario.tenant_id)
        cuota = await estado_cuota_loc(db, usuario.tenant_id)
        tickets = await uno(db, """
            SELECT count(*) FILTER (WHERE etapa = 'produccion')  AS cerrados,
                   count(*) FILTER (WHERE etapa NOT IN
                        ('produccion','cancelado','cerrado_duplicado')) AS en_proceso
              FROM core.tickets WHERE tenant_id = :t
               AND to_char(creado_en, 'YYYY-MM') <= :c""",
            {"t": usuario.tenant_id, "c": c})
    restantes = (cuota["limite"] - cuota["consumidas"]) if cuota["limite"] else None
    return {"ciclo": c,
            "plan": plan and {"codigo": plan["codigo"], "loc_mes": plan["loc_mes"],
                              "lineas": plan["lineas"], "renueva_el": str(plan["renueva_el"])},
            "loc": {"consumidas": cuota["consumidas"], "restantes": restantes,
                    "porcentaje": cuota["porcentaje"]},
            "tickets": tickets, "umbral": cuota["umbral"]}


@router.get("/by-ticket", summary="Consumo por ticket")
async def by_ticket(ciclo: str | None = Query(default=None),
                    page: int = Query(default=1, ge=1),
                    page_size: int = Query(default=25, ge=1, le=100),
                    usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: items[{ticket_id, titulo, tipo, loc_netas, tokens, etapa}] paginado."""
    c = _validar_ciclo(ciclo)
    async with sesion_de(usuario) as db:
        items = await todos(db, """
            SELECT t.numero AS ticket_id, t.titulo, t.tipo, t.etapa,
                   coalesce(sum(l.netas), 0)::int AS loc_netas,
                   coalesce((SELECT sum(u.tokens_in + u.tokens_out)
                               FROM core.usage_tokens u WHERE u.ticket_id = t.id), 0)::bigint AS tokens
              FROM core.tickets t
              LEFT JOIN core.usage_loc l ON l.ticket_id = t.id AND l.ciclo = :c
             WHERE t.tenant_id = :t AND to_char(t.creado_en, 'YYYY-MM') <= :c
             GROUP BY t.id ORDER BY max(t.actualizado_en) DESC
             LIMIT :lim OFFSET :off""",
            {"t": usuario.tenant_id, "c": c, "lim": page_size, "off": (page - 1) * page_size})
        total = (await uno(db, "SELECT count(*) AS n FROM core.tickets WHERE tenant_id=:t",
                           {"t": usuario.tenant_id}))["n"]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
