-- KC-BE-09-1: Consulta de identificación de registros en core.usage_loc
-- cuyo ciclo grabado por mes calendario difiere del ciclo de aniversario real del tenant.

SELECT
    l.id AS usage_loc_id,
    l.tenant_id,
    l.ticket_id,
    l.tipo,
    l.netas,
    l.creado_en,
    s.inicia_el AS suscripcion_inicia_el,
    l.ciclo AS ciclo_registrado,
    left(core.obtener_ciclo_cuota_actual(l.tenant_id, l.creado_en::date), 7) AS ciclo_aniversario_real
FROM core.usage_loc l
JOIN core.subscriptions s
  ON s.tenant_id = l.tenant_id
 AND s.finaliza_el IS NULL
 AND s.deleted_at IS NULL
WHERE l.ciclo <> left(core.obtener_ciclo_cuota_actual(l.tenant_id, l.creado_en::date), 7)
ORDER BY l.tenant_id, l.creado_en, l.id;
