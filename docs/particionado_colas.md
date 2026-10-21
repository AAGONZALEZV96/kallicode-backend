# Particionado de `core.webhook_inbox` y `core.notifications`, con purga por partición (KC-DB-07-3, DB-F09)

Continúa `docs/particionado_auditoria.md` (KC-DB-07-1) y `docs/particionado_consumo.md` (KC-DB-07-2).
`core.webhook_inbox` guarda el payload crudo de cada entrega de los proveedores y `core.notifications` una
fila por aviso y usuario. El §9.3 fija 90 días de retención para los webhooks procesados. Con la tabla
simple la purga era un `DELETE` masivo (el worker `housekeeping`, cada hora), que deja bloat; con tablas
particionadas por mes, vencer un mes es un `DROP` de la partición: instantáneo y sin bloat. Migración:
`db/migrations/036_particion_colas.sql`.

## 1. Estado final

| Aspecto | `core.webhook_inbox` | `core.notifications` |
|---|---|---|
| Partición | `PARTITION BY RANGE (recibido_mes)`, una por mes (UTC) | `PARTITION BY RANGE (creada_en)`, una por mes (UTC) |
| Pre-creadas | 12 meses futuros + el mes actual + `DEFAULT` | igual |
| Gestión | `pg_partman`: `1 month`, `premake = 12`, **sin retención propia** (ver sección 3) | igual |
| Clave primaria | `(recibido_mes, id)` | `(creada_en, id)` |
| Unicidad de entregas | `wi_delivery_uq (connection_id, delivery_id, recibido_mes)` | — |
| Claves ajenas compuestas | `(tenant_id, connection_id)` → `connections` (`CASCADE`); `(tenant_id, ticket_id)` → `tickets` | `(tenant_id, ticket_id)` → `tickets` y `(tenant_id, user_id)` → `users` (ambas `CASCADE`) |
| Índices (por partición) | `idx_webhook_inbox_tenant_connection_id`, `idx_webhook_inbox_tenant_ticket_id`, `webhook_inbox_pend_ix` (parcial), `webhook_inbox_tenant_ix` | `idx_notifications_tenant_ticket_id`, `idx_notifications_tenant_user_id`, `notifications_tenant_noleidas_ix` (parcial), `notifications_tenant_user_ix` |
| RLS y permisos | `tenant_isolation` + `worker_colas` (el worker barre todos los tenants); app `SELECT/INSERT/UPDATE/DELETE`, worker `SELECT/UPDATE/DELETE` | `tenant_isolation`; app `SELECT/INSERT/UPDATE/DELETE` |
| Retención por partición | 90 días (§9.3) | 90 días (propuesta, ver sección 3) |

## 2. Idempotencia de las entregas: `wi_delivery_uq`

**El problema.** Una restricción `UNIQUE` de una tabla particionada debe incluir la clave de partición. Si
se particionara por `recibido_en` (un instante distinto en cada entrega), `UNIQUE (connection_id,
delivery_id, recibido_en)` **nunca se violaría**: dos entregas idénticas tendrían `recibido_en` distinto y
la restricción dejaría de deduplicar, con lo que la misma entrega se normalizaría dos veces (dos tickets).

**La solución.** `webhook_inbox` se particiona por una columna nueva, `recibido_mes` (el primer día del mes
UTC de la recepción, `DEFAULT date_trunc('month', now() AT TIME ZONE 'UTC')::date`), y la unicidad pasa a ser
`(connection_id, delivery_id, recibido_mes)`: **una entrega no puede repetirse dentro del mismo mes**.
`wi_mes_chk` mantiene `recibido_mes` coherente con `recibido_en` (no se puede inventar un mes). La clave
primaria pasa a `(recibido_mes, id)` (la `017` la había dejado en `(recibido_en, id)`).

**Efecto sobre los reintentos de más de un mes** (lo pedía el ticket). La deduplicación vale dentro del mes
de la primera entrega: un reintento del proveedor que llegue en un mes distinto (p. ej. entre el 31 y el 1)
**no se reconoce como duplicado** y se inserta otra vez. Los reintentos reales de un proveedor llegan en
minutos u horas, así que el caso afecta solo a la frontera de mes; si en algún proveedor fuera relevante,
habría que deduplicar también por una tabla pequeña no particionada. Lo fija el test
`test_el_efecto_documentado_un_reintento_de_otro_mes_no_se_reconoce`. Purgar un mes (sección 3) reabre la
posibilidad de repetir una entrega de ese mes, que ya tiene más de 90 días.

**Cambio en la API** (`apps/api/.../routers/webhooks.py`): el `INSERT` usaba `ON CONFLICT (connection_id,
delivery_id) DO NOTHING`; un objetivo de dos columnas **ya no coincide con ninguna restricción** y PostgreSQL
lo rechaza (`test_on_conflict_con_objetivo_de_dos_columnas...`). Pasa a `ON CONFLICT DO NOTHING` sin objetivo,
que ignora cualquier violación de unicidad y devuelve el mismo resultado (`RETURNING id` vacío). Es el único
cambio de código de la API.

## 3. Purga por partición

`core.purgar_particiones_vencidas(p_ahora, p_simular)` elimina (`DROP TABLE`) las particiones cuyo límite
superior ya cumplió el plazo de `core.retencion_particiones`: **todas** sus filas son más antiguas que la
retención. **Nunca ejecuta `DELETE`** (lo comprueba `test_la_purga_nunca_usa_delete`). Devuelve, por cada
partición candidata, qué hizo (`eliminada`, `omitida` o `simulada`) y por qué.

- **Salvaguardas.** Una partición de `webhook_inbox` con entregas `pendiente` (sin procesar) **no se
  elimina**: queda `omitida`, con el motivo, para que alguien las atienda (borrarla perdería trabajo).
  Entregas `procesado`, `ignorado` y `fallido` sí se descartan con su partición. Si no obtiene el bloqueo en
  5 s (consultas largas sobre la tabla), omite la partición y reintenta en la siguiente ejecución. Nunca toca
  la `DEFAULT`, el mes en curso ni los futuros.
- **Quién la ejecuta.** El worker `housekeeping` (cada hora, como `kallicode_admin`, dueño de las
  particiones) llama a la función en lugar del antiguo `DELETE FROM core.webhook_inbox ...` y registra qué
  eliminó y qué omitió (`housekeeping.particion_omitida` en el log, a nivel `warning`).
- **Por qué no la retención de `pg_partman`.** Su retención borraría la partición sin mirar si quedan
  entregas pendientes; por eso `part_config.retention` queda en `NULL` y la purga es esta función.
- **Plazos** (`core.retencion_particiones`, global, solo `kallicode_admin`): `webhook_inbox` **90 días** (§9.3)
  y `notifications` **90 días**. **El ticket no fija un plazo para `notifications`; el de 90 días es una
  propuesta** (el mismo del §9.3) y se cambia con un `UPDATE` de esa tabla, sin tocar código. Para no
  purgar una tabla se borra su fila.
- **No es por tenant.** Una partición se elimina entera, así que el plazo es por tabla. `core.retention_policies`
  (034, por tenant) no gobierna esto.
- **Ventana.** Se purgan meses completos: con 90 días de plazo, un mes se elimina cuando han pasado 90 días
  desde su último día; las filas más recientes de un mes pueden vivir hasta ~120 días.

## 4. La cola sigue funcionando

`webhook_inbox_pend_ix (estado, recibido_en) WHERE estado IN ('pendiente','fallido')` queda como un índice
parcial **por partición**. `EXPLAIN` (en `test_el_indice_parcial_de_la_cola_...`) como `kallicode_worker`: el
barrido del normalizador (`WHERE estado IN ('pendiente','fallido') ORDER BY recibido_en LIMIT 50`) usa el
índice parcial de la partición del mes en curso y de las demás, sin `Seq Scan`; la lectura de una entrega por
`id` tampoco recorre ninguna partición entera. El barrido no filtra por fecha, así que consulta el índice
parcial de todas las particiones (las futuras están vacías: es barato; el índice parcial solo contiene las
filas sin procesar).

## 5. Seguridad: el event trigger ahora cubre `ATTACH`

Se amplió el *event trigger* de KC-DB-07-2 (`db/00_extensiones.sql`). `pg_partman` puede crear una partición
de dos maneras: `CREATE TABLE ... PARTITION OF` (la tabla ya es partición al terminar el comando, y el
trigger la ve) o `CREATE TABLE` suelta + `ALTER TABLE padre ATTACH PARTITION` (en el `CREATE` todavía no es
partición, y el comando del `ATTACH` reporta al **padre**). Con `036` se observó la segunda ruta: el trigger
de 07-2 no la veía y las particiones quedaban con los permisos por defecto de `04_rls.sql` (acceso a todos
los tenants, sin RLS); la comprobación final de la propia migración lo detectó. El trigger ahora mira el
objeto del comando y, si es una tabla particionada, **todas sus particiones**. Además la migración `036`
hace un barrido retroactivo de las particiones de `core` y `vec` que existan (por si alguna base quedó
expuesta con 07-2) y comprueba al terminar que ni la aplicación ni el worker tienen permisos sobre ninguna.
Regresión:
`test_el_event_trigger_cubre_una_particion_creada_con_attach`.

## 6. Aplicar la migración

Mismos requisitos que 07-1 y 07-2 (`pg_partman`, `00_extensiones.sql` como superusuario). **Hay que volver a
ejecutar `db/00_extensiones.sql`** para actualizar la función del event trigger (es `CREATE OR REPLACE`).
La copia va mes a mes, se verifica (filas, ids y huella) y, si algo no cuadra, se revierte entera. Durante la
copia cada tabla está bloqueada para escritura (`lock_timeout = 30 s`). Es idempotente.

```bash
psql -U postgres -d <base> -f db/00_extensiones.sql     # extensión, permisos y event trigger (actualizado)
uv run python packages/core/migrate.py                  # aplica la 036
```

## 7. Decisiones y desviaciones respecto del ticket

- **Número de migración:** el ticket nombra `026_particion_colas.sql`; la 026 ya existe y la 034 y la 035 están
  tomadas. Es la `036`.
- **`wi_delivery_uq`:** el ticket dice que «la unicidad incluye la clave de partición». Con `recibido_en` como
  clave eso anularía la deduplicación (sección 2); por eso la clave de partición es `recibido_mes`. Es un
  cambio de diseño respecto de la PK de la `017`.
- **Plazo de `notifications`:** no está en el ticket; 90 días es una propuesta a confirmar.
- **Código fuera de la base de datos** (necesario para que el ticket funcione): `routers/webhooks.py` (`ON
  CONFLICT` sin objetivo) y `housekeeping.py` (purga por función en vez de `DELETE`).
- **Pruebas existentes adaptadas:** `test_pk_particion.py` (la PK de `webhook_inbox`), `test_rol_worker.py`
  y `test_convencion_indices.py` (el plan nombra el índice de cada partición; se resuelve a su índice padre).
- **Autovacuum:** no se toca (no es de este ticket); `webhook_inbox` recibe muchos `UPDATE` de estado y se
  puede estudiar aparte.
- **`UPDATE notifications ... WHERE id = :id`** (marcar leída) no lleva `creada_en`: recorre las particiones
  (el índice `(tenant_id, user_id)` lo resuelve en cada una). Con la purga a 90 días hay pocas con datos.
