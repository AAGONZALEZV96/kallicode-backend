"""Consumo (§15.1–15.2 del diseño): resumen del ciclo y análisis por ticket."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from kallicode_core.comercial import ciclo_de, estado_cuota_loc, estado_storage, plan_vigente
from kallicode_core.db import uno
from kallicode_core.errors import AppError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import UsuarioActual, sesion_de, usuario_actual
from ..paginacion import paginar, parse_sort

_SORT_USAGE = {
    "actualizado_en": "max(t.actualizado_en)",
    "titulo": "t.titulo",
    "tipo": "t.tipo",
    "etapa": "t.etapa",
    "loc_netas": "loc_netas",
    "tokens": "tokens",
    "tiempo_e2e": "tiempo_e2e_s",
}
_CAMPO_SORT = {columna: campo for campo, columna in _SORT_USAGE.items()}

_TIEMPO_E2E = """extract(epoch FROM coalesce(
        t.cerrado_en,
        CASE WHEN t.etapa IN ('produccion', 'cancelado', 'cerrado_duplicado')
             THEN t.actualizado_en END,
        now()) - t.creado_en)::bigint"""

_PATRON_CICLO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

router = APIRouter(prefix="/usage", tags=["Consumo y plan"])


async def _validar_ciclo(db: AsyncSession, tenant_id: str, ciclo: str | None) -> str:
    actual = await ciclo_de(db, tenant_id)
    c = ciclo or actual
    if not _PATRON_CICLO.match(c) or c > actual:
        raise AppError("CICLO_INVALIDO", 422, "El ciclo indicado no existe.")
    return c


@router.get("/summary", summary="Resumen de consumo del ciclo")
async def summary(
    ciclo: str | None = Query(default=None),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    """Salida: plan, loc {consumidas, restantes, porcentaje}, tickets, tiempo_e2e, umbral,
    almacenamiento {usados_bytes, limite_bytes, porcentaje, umbral} y cola {tickets, desde}.

    almacenamiento es el uso de evidencia del plan vigente con los umbrales de QU-3 (aviso desde
    el 80 %, agotado al llegar al límite; limite_bytes y porcentaje null si el plan no lo fija).
    cola son los tickets que esperan en en_cola_por_cuota ahora (no depende del ciclo) y desde
    cuándo espera el más antiguo (null si no hay).
    """
    async with sesion_de(usuario) as db:
        c = await _validar_ciclo(db, usuario.tenant_id, ciclo)
        plan = await plan_vigente(db, usuario.tenant_id)
        cuota = await estado_cuota_loc(db, usuario.tenant_id, ciclo=c)
        tickets = await uno(
            db,
            """
            SELECT count(*) FILTER (WHERE etapa = 'produccion')  AS cerrados,
                   count(*) FILTER (WHERE etapa NOT IN
                        ('produccion','cancelado','cerrado_duplicado')) AS en_proceso
              FROM core.tickets WHERE tenant_id = :t
               AND left(core.obtener_ciclo_cuota_actual(CAST(:t AS text), creado_en::date), 7) <= :c""",
            {"t": usuario.tenant_id, "c": c},
        )
        fabrica = await uno(
            db,
            f"""
            SELECT count(*)::int AS cerrados,
                   (percentile_cont(0.5) WITHIN GROUP (ORDER BY {_TIEMPO_E2E}))::bigint
                       AS mediana_s
              FROM core.tickets t
             WHERE t.tenant_id = :t AND t.etapa = 'produccion'
               AND left(core.obtener_ciclo_cuota_actual(CAST(:t AS text), t.cerrado_en::date), 7) = :c""",
            {"t": usuario.tenant_id, "c": c},
        )
        almacenamiento = await estado_storage(db, usuario.tenant_id)
        cola = await uno(
            db,
            """
            SELECT count(*)::int AS tickets, min(creado_en) AS desde
              FROM core.tickets
             WHERE tenant_id = :t AND etapa = 'en_cola_por_cuota' AND deleted_at IS NULL""",
            {"t": usuario.tenant_id},
        )
    restantes = (cuota["limite"] - cuota["consumidas"]) if cuota["limite"] else None
    return {
        "ciclo": c,
        "plan": plan
        and {
            "codigo": plan["codigo"],
            "loc_mes": plan["loc_mes"],
            "lineas": plan["lineas"],
            "renueva_el": str(plan["renueva_el"]),
        },
        "loc": {
            "consumidas": cuota["consumidas"],
            "restantes": restantes,
            "porcentaje": cuota["porcentaje"],
        },
        "tickets": tickets,
        "tiempo_e2e": {"mediana_s": fabrica["mediana_s"], "cerrados": fabrica["cerrados"]},
        "umbral": cuota["umbral"],
        "almacenamiento": almacenamiento,
        "cola": {
            "tickets": cola["tickets"],
            "desde": cola["desde"].isoformat() if cola["desde"] else None,
        },
    }


@router.get("/by-ticket", summary="Consumo por ticket")
async def by_ticket(
    ciclo: str | None = Query(default=None),
    sort: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    columna, ascending = parse_sort(sort, _SORT_USAGE, default=("actualizado_en", "desc"))
    direccion = "ASC" if ascending else "DESC"
    async with sesion_de(usuario) as db:
        c = await _validar_ciclo(db, usuario.tenant_id, ciclo)
        where = (
            "t.tenant_id = :t "
            "AND left(core.obtener_ciclo_cuota_actual(CAST(:t AS text), t.creado_en::date), 7) <= :c"
        )
        items, total = await paginar(
            db,
            tabla="core.tickets t",
            joins="LEFT JOIN core.usage_loc l ON l.ticket_id = t.id AND l.ciclo = :c",
            columnas=f"""t.numero AS ticket_id, t.titulo, t.tipo, t.etapa,
                   coalesce(sum(l.netas), 0)::int AS loc_netas,
                   coalesce((SELECT sum(u.tokens_in + u.tokens_out)
                               FROM core.usage_tokens u WHERE u.ticket_id = t.id), 0)::bigint AS tokens,
                   {_TIEMPO_E2E} AS tiempo_e2e_s""",
            where=where,
            params={"t": usuario.tenant_id, "c": c},
            group_by="t.id",
            order_by=f"{columna} {direccion}, t.numero",
            page=page,
            page_size=page_size,
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": f"{'-' if not ascending else ''}{_CAMPO_SORT[columna]}",
    }
