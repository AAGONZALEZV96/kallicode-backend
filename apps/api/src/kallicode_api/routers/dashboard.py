"""Tablero (§8 del diseño): KPIs, líneas y kanban. Solo lectura."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from kallicode_core.comercial import plan_vigente
from kallicode_core.db import todos, uno

from ..deps import UsuarioActual, sesion_de, usuario_actual

router = APIRouter(prefix="/dashboard", tags=["Tablero"])

_ACTIVAS = "('triage','design','build','qa','security','deploy')"


@router.get("/summary", summary="KPIs del tablero")
async def summary(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: en_cola, en_proceso, esperando_aprobacion, en_produccion_30d."""
    async with sesion_de(usuario) as db:
        return await uno(db, f"""
            SELECT count(*) FILTER (WHERE etapa = 'triage')                      AS en_cola,
                   count(*) FILTER (WHERE etapa IN {_ACTIVAS} AND etapa<>'triage') AS en_proceso,
                   count(*) FILTER (WHERE gate_pendiente IS NOT NULL)            AS esperando_aprobacion,
                   count(*) FILTER (WHERE etapa = 'produccion'
                                     AND cerrado_en > now() - interval '30 days') AS en_produccion_30d
              FROM core.tickets WHERE tenant_id = :t""", {"t": usuario.tenant_id})


@router.get("/lines", summary="Estado de líneas de producción")
async def lines(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: lineas[{numero, estado, ticket_id, etapa}], en_espera, contratadas (QU-2)."""
    async with sesion_de(usuario) as db:
        lineas = await todos(db, """
            SELECT pl.numero, pl.estado, t.numero AS ticket_id, j.estado AS etapa
              FROM core.production_lines pl
              LEFT JOIN core.jobs j ON j.id = pl.job_id
              LEFT JOIN core.tickets t ON t.id = j.ticket_id
             WHERE pl.tenant_id = :t ORDER BY pl.numero""", {"t": usuario.tenant_id})
        en_espera = (await uno(db, """SELECT count(*) AS n FROM core.jobs
                                       WHERE tenant_id=:t AND estado='pendiente_asignacion'""",
                               {"t": usuario.tenant_id}))["n"]
        plan = await plan_vigente(db, usuario.tenant_id)
    return {"lineas": lineas, "en_espera": en_espera,
            "contratadas": plan["lineas"] if plan else None}


@router.get("/kanban", summary="Kanban por etapa")
async def kanban(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: columnas {etapa: [tarjetas]} — 20 más recientes por columna."""
    async with sesion_de(usuario) as db:
        filas = await todos(db, f"""
            SELECT numero AS id, titulo, tipo, prioridad, etapa, gate_pendiente
              FROM (SELECT *, row_number() OVER (PARTITION BY etapa
                                                 ORDER BY creado_en DESC) AS rn
                      FROM core.tickets
                     WHERE tenant_id = :t
                       AND etapa IN {_ACTIVAS} OR etapa = 'produccion') q
             WHERE rn <= 20""", {"t": usuario.tenant_id})
    columnas: dict[str, list] = {}
    for f in filas:
        columnas.setdefault(f.pop("etapa"), []).append(f)
    return {"columnas": columnas}
