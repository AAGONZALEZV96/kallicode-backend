# Acta de conversión de ciclos acumulados a facturación por aniversario (KC-BE-09-1)

- **Historia:** KC-BE-09-1 (Épica EBE6)
- **Responsables:** BE4 (Producto y Comercial) · Database (DBA)
- **Referencia de diseño:** D13 · `db/migrations/026_ciclo_cuota_aniversario.sql` · `db/migrations/051_renovacion_ciclo.sql`

## 1. Contexto y alcance

Hasta KC-BE-09-1, el backend derivaba el ciclo de facturación mediante `date.today().strftime('%Y-%m')`, imputando al mes calendario las líneas de `core.usage_loc` creadas entre el día 1 del mes y el día anterior al aniversario de contratación (`core.subscriptions.inicia_el`).

Con la delegación en `core.obtener_ciclo_cuota_actual(tenant_id, fecha)` (cuya clave de inicio de ciclo `YYYY-MM` coincide con `core.renovar_ciclo` y `loc_ciclo_chk`), cada registro de consumo se imputa al ciclo de aniversario correspondiente al instante de creación.

## 2. Consulta de identificación versionada

La consulta oficial de diagnóstico vive en `db/tools/identificar_ciclos_aniversario.sql`:

~~~sql
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
~~~

## 3. Procedimiento acordado (Staging → Producción)

Dado que `core.usage_loc` está protegida por el trigger append-only `trg_probatoria_append_only` (`027_append_only_probatorias.sql`), cualquier reclasificación histórica sigue el protocolo Clase B (`docs/politica-cambios-esquema.md`):

1. **Punto de restauración (PITR):** verificar backup/WAL reciente antes de intervenir.
2. **Diagnóstico:** ejecutar `psql -f db/tools/identificar_ciclos_aniversario.sql` y guardar el listado de `usage_loc_id` afectados. Si el conteo es `0`, no se requiere ventana de mutación.
3. **Ejecución en staging primero:** dentro de una transacción única como `kallicode_admin`:
   - Desactivar temporalmente `trg_probatoria_append_only` en `core.usage_loc` (`ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only`).
   - Actualizar `core.usage_loc.ciclo = left(core.obtener_ciclo_cuota_actual(tenant_id, creado_en::date), 7)` para los IDs identificados; el trigger de cuota (`028_cuota_delta.sql`) recalcula automáticamente `core.usage_cycles` en el ciclo origen y en el ciclo destino.
   - Reactivar `trg_probatoria_append_only` (`ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only`).
   - Ejecutar `SELECT * FROM core.validar_integridad();` y verificar que `V10`, `V22` y `V23` reportan `0` problemas.
4. **Promoción a producción:** repetir el mismo procedimiento verificado en staging tras validar los totales en `GET /api/v1/usage/summary`.
