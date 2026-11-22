"""Resumen diario de tickets en cola por cuota."""

from kallicode_core.db import sesion_sistema, sesion_tenant, todos
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from sqlalchemy import text


async def ciclo() -> None:
    async with sesion_sistema() as db:
        tenants = await todos(
            db,
            """SELECT DISTINCT tenant_id FROM core.tickets
               WHERE etapa = 'en_cola_por_cuota' AND deleted_at IS NULL""",
        )
    for fila in tenants:
        tenant = fila["tenant_id"]
        try:
            await _avisar(tenant)
        except Exception:
            log.exception("avisos_cuota.fallo", tenant_id=tenant)


async def _avisar(tenant: str) -> None:
    async with sesion_tenant(tenant, actor="svc:billing") as db:
        # La reserva y el aviso se confirman juntos. La clave caduca con el TTL de 48 h.
        await db.execute(
            text("""
                WITH owners AS (
                    SELECT id FROM core.users
                     WHERE tenant_id = :t AND rol = 'owner' AND estado = 'activo'
                       AND deleted_at IS NULL
                ), pendientes AS (
                    SELECT string_agg(
                               format('%s (%s d de antigüedad)', coalesce(numero, id),
                                      greatest(0, floor(extract(epoch FROM
                                          (now() - creado_en)) / 86400))),
                               chr(10) ORDER BY creado_en, id
                           ) AS resumen
                      FROM core.tickets
                     WHERE tenant_id = :t AND etapa = 'en_cola_por_cuota'
                       AND deleted_at IS NULL
                ), reserva AS (
                    INSERT INTO core.idempotency_keys
                        (tenant_id, endpoint, clave, respuesta, status_code)
                    SELECT :t, 'avisos_cuota_diarios', (now() AT TIME ZONE 'UTC')::date::text,
                           '{}'::jsonb, 200
                      FROM pendientes
                     WHERE resumen IS NOT NULL AND EXISTS (SELECT 1 FROM owners)
                    ON CONFLICT DO NOTHING
                    RETURNING tenant_id
                )
                INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, cuerpo)
                SELECT :id || '_' || o.id, r.tenant_id, o.id, 'aviso_cuota',
                       'Tickets en cola por cuota', p.resumen
                  FROM reserva r CROSS JOIN owners o CROSS JOIN pendientes p
            """),
            {"id": nuevo("nt"), "t": tenant},
        )
