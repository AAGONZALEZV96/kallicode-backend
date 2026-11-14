# Purgas de retención gobernadas por `core.retention_policies` (KC-DB-10-1)

Migración `054_purgas_retencion.sql`. Prueba: `packages/core/tests/integracion/test_retencion_purgas.py`.

## 1. Idea

Los plazos de retención viven **solo** en `core.retention_policies`. Un motor, `core.purgar_retencion()`, los lee en
cada ejecución y purga lo vencido; `pg_cron` lo lanza cada noche (`retencion_purgas`, 03:20 UTC). **Ningún plazo
está escrito en el SQL**: cambiar `retention_days` de una fila cambia lo que hará la siguiente ejecución (lo
comprueban `test_ningun_plazo_de_retencion_esta_escrito_en_el_sql` y
`test_cambiar_los_dias_en_la_politica_cambia_la_siguiente_ejecucion`).

Antes había tres sitios con plazos o declaraciones (`retention_policies`, sin consumidor; `retencion_particiones`,
con los 90 días de `webhook_inbox` y `notifications`; y `retencion_declaraciones`, de la validación V26). Se
fusionaron en `retention_policies`; las otras dos tablas se eliminaron.

## 2. Qué dice una política

| Columna | Significado |
|---|---|
| `tenant_id` | `NULL` = política de **plataforma** (la que manda); con valor = política de ese tenant. |
| `entity_name` | `esquema.tabla`. |
| `action` | `drop` (eliminar), `detach` (desacoplar la partición, se conserva la tabla), `archive` (desacoplar y mover a `retencion_archivo`) o `conservar` (declara que **no se purga**; no lleva plazo). |
| `retention_days` | Días de retención (obligatorio salvo en `conservar`). |
| `columna_tiempo` | Columna de fecha de cada fila; solo hace falta en tablas **no particionadas**. |
| `lote` | Filas por lote en el borrado por filas (100–100.000, por defecto 5.000). |
| `is_active` | Una política desactivada no se ejecuta **ni cuenta como declarada** para V26. |

Políticas de plataforma sembradas: `core.webhook_inbox` y `core.notifications` (`drop`, 90 días, los plazos que
había), y `conservar` para `audit.audit_events`, `core.usage_tokens`, `core.job_transitions`,
`vec.definition_embeddings` y `vec.doc_chunks`, cada una con su motivo. **`conservar` recoge la situación de
hecho, no es una decisión nueva**: cuando se decida una ventana para consumo o transiciones, basta un `UPDATE`
(`action = 'drop'`, `retention_days = N`).

## 3. Qué hace el motor

`SELECT * FROM core.purgar_retencion(p_ahora, p_simular, p_solo, p_max_lotes)`; `p_simular = true` informa sin
tocar nada; `p_solo` limita a `particion` o `filas`.

* **Modo partición** (tablas particionadas por rango de tiempo con política de plataforma): elimina (`DROP`),
  desacopla (`DETACH`) o archiva las particiones cuyo **límite superior** ya cumplió el plazo, de modo que **todas**
  sus filas son más antiguas. Nunca usa `DELETE` y nunca toca la partición `DEFAULT`, la del mes en curso ni las
  futuras. Antes de descartar consulta `core.puede_descartar_particion()`.
* **Modo filas** (tablas no particionadas con `columna_tiempo`): borra por **lotes acotados** con
  `FOR UPDATE SKIP LOCKED` (`core.borrar_lote_retencion`): no espera a filas bloqueadas por otras transacciones
  y cada lote es una operación corta. Con `p_max_lotes` (por defecto 100) limita el trabajo de una ejecución y,
  si quedan filas vencidas, lo dice en el motivo; la siguiente ejecución sigue.
* **Políticas por tenant.** Solo **acortan** el plazo de la política de plataforma de esa entidad (borran las filas
  de ese tenant más antiguas que sus días) y solo son `drop`. Una más larga que la de plataforma se ignora
  (`ignorada`): una partición se elimina entera para todos. Y solo actúan sobre entidades que la plataforma ya
  gobierna: **el nombre de la tabla nunca sale de lo que escribe un tenant**, de modo que no es una vía de inyección
  ni de borrado de tablas arbitrarias (`test_una_politica_por_tenant_sin_politica_de_plataforma_no_toca_nada`).
* **Registro.** Cada acción real queda en `core.retention_purgas` (entidad, objeto, acción, filas, motivo). No se
  registran las simulaciones ni las ejecuciones sin nada vencido. Es la base del informe de cumplimiento (KC-DB-10-4).

## 4. Gancho previo al descarte

`core.puede_descartar_particion(tabla, partición)` devuelve `(permitido, motivo)`:

* `core.webhook_inbox`: bloquea una partición con entregas `pendiente` (se conserva el comportamiento de la 036).
* `audit.audit_events`: **bloquea siempre** («sin comprobante de export WORM verificado»). KC-DB-10-2 sustituye esta
  rama por la comprobación del comprobante real; mientras tanto, aunque alguien cambie la política de la
  auditoría a `drop`, el motor no elimina ninguna partición (`test_el_gancho_impide_descartar_la_auditoria...`).

## 5. Quién la ejecuta y con qué permisos

* **pg_cron**: `SELECT * FROM core.purgar_retencion()` diario (una sola sentencia, como exige pg_cron). La migración
  lo programa si `pg_cron` está instalada y `kallicode_admin` tiene `USAGE` sobre el esquema `cron`; si no, avisa y
  hay que programarlo con un superusuario.
* **housekeeping** sigue llamando cada hora a `core.purgar_particiones_vencidas()`, ahora un envoltorio del motor en
  modo partición con las mismas columnas de salida.
* Las funciones y `core.retention_purgas` son solo de `kallicode_admin`; la aplicación no puede ejecutar el motor ni
  ver las políticas de plataforma (RLS: las filas con `tenant_id` NULL no son visibles para ella).

## 6. Diferencias con el runbook anterior y límites

* El runbook de particionado pide `DETACH PARTITION ... CONCURRENTLY`. **Una función no puede ejecutarlo** (no se
  admite dentro de una transacción), así que el motor usa `DETACH` normal con `lock_timeout` de 5 s: si no obtiene
  el bloqueo, omite la partición y lo reintenta en la siguiente ejecución. Para un desacople concurrente manual
  sigue valiendo el protocolo del runbook.
* «Sin bloquear tablas activas» se garantiza así: el borrado por filas no espera a las filas bloqueadas ni retiene
  bloqueos más de un lote; el descarte de particiones espera como máximo 5 s y, si no puede, se salta la partición.
  No se midió el efecto sobre la latencia de una carga real; la prueba comprueba que el motor termina sin esperar a
  una fila bloqueada por otra transacción.
* La purga de particiones es por **meses completos** (ver `docs/particionado_colas.md`): las filas más recientes
  de un mes pueden vivir hasta ~30 días más que el plazo.
* La auditoría, el consumo de tokens y las transiciones de trabajos **no se purgan** hoy (`conservar`).
