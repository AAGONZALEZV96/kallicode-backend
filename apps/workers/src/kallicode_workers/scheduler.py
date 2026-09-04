"""Planificador de líneas (QU-2): asigna jobs pendientes a líneas libres.

Corre cada 5 s. Toma por tenant el job pendiente más prioritario (P1..P4,
luego antigüedad) y la primera línea disponible, en UNA transacción con
FOR UPDATE: dos réplicas del worker no pueden asignar la misma línea.
"""
from __future__ import annotations

from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.logging import log


async def ciclo() -> None:
    async with sesion_sistema() as db:
        tenants = await todos(db, """
            SELECT DISTINCT tenant_id FROM core.jobs
             WHERE estado = 'pendiente_asignacion'""")
    for fila in tenants:
        await _asignar_tenant(fila["tenant_id"])


async def _asignar_tenant(tenant: str) -> None:
    while True:
        async with sesion_tenant(tenant, actor="svc:scheduler") as db:
            job = await uno(db, """
                SELECT j.*, t.numero AS ticket_numero FROM core.jobs j
                  JOIN core.tickets t ON t.id = j.ticket_id
                 WHERE j.tenant_id = :t AND j.estado = 'pendiente_asignacion'
                 ORDER BY j.prioridad, j.creado_en
                 FOR UPDATE OF j SKIP LOCKED LIMIT 1""", {"t": tenant})
            if not job:
                return
            linea = await uno(db, """
                SELECT numero FROM core.production_lines
                 WHERE tenant_id = :t AND estado = 'disponible'
                 ORDER BY numero FOR UPDATE SKIP LOCKED LIMIT 1""", {"t": tenant})
            if not linea:
                return  # QU-2: sin línea libre, el job espera
            await db.execute(text("""UPDATE core.jobs SET linea=:n, estado='triage'
                                      WHERE id=:j"""),
                             {"n": linea["numero"], "j": job["id"]})
            await registrar_evento(db, tenant, evento="job_transicion", etapa="triage",
                                   resumen=f"Asignado a línea {linea['numero']}",
                                   actor_tipo="sistema", ticket_id=job["ticket_numero"],
                                   job_id=job["id"])
        log.info("scheduler.asignado", job_id=job["id"], linea=linea["numero"],
                 tenant_id=tenant)
