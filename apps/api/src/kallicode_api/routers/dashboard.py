"""Tablero (§8 del diseño): KPIs, líneas y kanban. Solo lectura."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from kallicode_core.comercial import plan_vigente
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError

from ..deps import UsuarioActual, sesion_de, usuario_actual
from ..paginacion import codificar_cursor, decodificar_cursor

router = APIRouter(prefix="/dashboard", tags=["Tablero"])

_ETAPAS_ACTIVAS = ("triage", "design", "build", "qa", "security", "deploy")
_ETAPAS_KANBAN = (*_ETAPAS_ACTIVAS, "produccion")
_ACTIVAS = "('triage','design','build','qa','security','deploy')"


@router.get("/summary", summary="KPIs del tablero")
async def summary(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: en_cola, en_proceso, esperando_aprobacion, en_produccion_30d."""
    async with sesion_de(usuario) as db:
        return await uno(
            db,
            f"""
            SELECT count(*) FILTER (WHERE etapa = 'triage')                      AS en_cola,
                   count(*) FILTER (WHERE etapa IN {_ACTIVAS} AND etapa<>'triage') AS en_proceso,
                   count(*) FILTER (WHERE gate_pendiente IS NOT NULL)            AS esperando_aprobacion,
                   count(*) FILTER (WHERE etapa = 'produccion'
                                     AND cerrado_en > now() - interval '30 days') AS en_produccion_30d
              FROM core.tickets WHERE tenant_id = :t""",
            {"t": usuario.tenant_id},
        )


@router.get("/lines", summary="Estado de líneas de producción")
async def lines(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: lineas[{numero, estado, ticket_id, etapa}], en_espera, contratadas (QU-2)."""
    async with sesion_de(usuario) as db:
        lineas = await todos(
            db,
            """
            SELECT pl.numero, pl.estado, t.numero AS ticket_id, j.estado AS etapa
              FROM core.production_lines pl
              LEFT JOIN core.jobs j ON j.id = pl.job_id
              LEFT JOIN core.tickets t ON t.id = j.ticket_id
             WHERE pl.tenant_id = :t ORDER BY pl.numero""",
            {"t": usuario.tenant_id},
        )
        en_espera = (
            await uno(
                db,
                """SELECT count(*) AS n FROM core.jobs
                                       WHERE tenant_id=:t AND estado='pendiente_asignacion'""",
                {"t": usuario.tenant_id},
            )
        )["n"]
        plan = await plan_vigente(db, usuario.tenant_id)
    return {
        "lineas": lineas,
        "en_espera": en_espera,
        "contratadas": plan["lineas"] if plan else None,
    }


@router.get("/kanban", summary="Kanban por etapa")
async def kanban(
    etapa: str | None = Query(
        default=None, description="Filtrar una columna específica del kanban."
    ),
    cursor: str | None = Query(
        default=None,
        max_length=256,
        description="Cursor opaco de continuación emitido por la respuesta anterior.",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Máximo de tarjetas por columna."),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    """Salida: columnas, totales por columna y cursores de continuación (KC-BE-09-2)."""
    if etapa is not None and etapa not in _ETAPAS_KANBAN:
        raise AppError(
            "VALIDACION_ENTRADA",
            422,
            f"La etapa {etapa!r} no pertenece al tablero kanban.",
            {"etapas_permitidas": list(_ETAPAS_KANBAN)},
        )
    if cursor is not None and etapa is None:
        raise AppError(
            "VALIDACION_ENTRADA",
            422,
            "Para paginar con cursor debes indicar la etapa de la columna.",
        )

    cursor_pos = decodificar_cursor(cursor) if cursor is not None else None
    etapas_objetivo = (etapa,) if etapa is not None else _ETAPAS_KANBAN

    where_base = "tenant_id = :t AND etapa = ANY(:etapas)"
    params: dict[str, Any] = {
        "t": usuario.tenant_id,
        "etapas": list(etapas_objetivo),
        "lim_mas_uno": limit + 1,
    }

    where_cursor = ""
    if cursor_pos is not None:
        where_cursor = "AND (creado_en, numero) < (:cursor_ts, :cursor_num)"
        params["cursor_ts"] = cursor_pos[0]
        params["cursor_num"] = cursor_pos[1]

    async with sesion_de(usuario) as db:
        conteos = await todos(
            db,
            f"""
            SELECT etapa, count(*)::int AS total
              FROM core.tickets
             WHERE {where_base}
             GROUP BY etapa
            """,
            {"t": usuario.tenant_id, "etapas": list(etapas_objetivo)},
        )
        filas = await todos(
            db,
            f"""
            SELECT numero AS id, titulo, tipo, prioridad, etapa, gate_pendiente, creado_en
              FROM (
                    SELECT numero, titulo, tipo, prioridad, etapa, gate_pendiente, creado_en,
                           row_number() OVER (
                               PARTITION BY etapa
                               ORDER BY creado_en DESC, numero DESC
                           ) AS rn
                      FROM core.tickets
                     WHERE {where_base}
                       {where_cursor}
                   ) q
             WHERE rn <= :lim_mas_uno
             ORDER BY etapa, creado_en DESC, id DESC
            """,
            params,
        )

    totales_por_etapa = {col: 0 for col in etapas_objetivo}
    for fila in conteos:
        totales_por_etapa[fila["etapa"]] = int(fila["total"])

    agrupadas: dict[str, list[dict[str, Any]]] = {col: [] for col in etapas_objetivo}
    for fila in filas:
        agrupadas[fila["etapa"]].append(fila)

    columnas: dict[str, list[dict[str, Any]]] = {}
    cursores: dict[str, str | None] = {}
    for col in etapas_objetivo:
        lista = agrupadas[col]
        hay_mas = len(lista) > limit
        pagina = lista[:limit]
        if hay_mas and pagina:
            ultima = pagina[-1]
            cursores[col] = codificar_cursor(ultima["creado_en"], ultima["id"])
        else:
            cursores[col] = None

        tarjetas = []
        for item in pagina:
            item.pop("etapa", None)
            item.pop("creado_en", None)
            tarjetas.append(item)
        if tarjetas or etapa is not None:
            columnas[col] = tarjetas

    return {
        "columnas": columnas,
        "totales": totales_por_etapa,
        "cursores": cursores,
        "next_cursor": cursores[etapa] if etapa is not None else None,
    }
