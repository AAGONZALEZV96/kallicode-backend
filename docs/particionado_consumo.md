# Particionado de `core.usage_tokens` y `core.job_transitions` (KC-DB-07-2, DB-F09)

Continúa `docs/particionado_auditoria.md` (KC-DB-07-1) con el mismo patrón. `core.usage_tokens` acumula
una fila por invocación de modelo y `core.job_transitions` una por cambio de estado de job; las dos crecen
sin techo y rotan mucho. Desde `db/migrations/035_particion_consumo.sql` están particionadas por rango
mensual de `creado_en` (UTC) con `pg_partman`. Sus claves primarias ya incluían `creado_en`
(`017_pk_para_particion.sql`).

## 1. Estado final

| Aspecto | `core.usage_tokens` | `core.job_transitions` |
|---|---|---|
| Partición | `PARTITION BY RANGE (creado_en)`, una por mes (UTC) | igual |
| Pre-creadas | 12 meses futuros + el mes actual + `DEFAULT` | igual |
| Gestión | `pg_partman`: `1 month`, `premake = 12`, sin retención | igual |
| Clave primaria | `(creado_en, id)` | `(creado_en, id)` |
| Claves ajenas compuestas | `(tenant_id, ticket_id)` → `core.tickets(tenant_id, id)`, `ON DELETE CASCADE`, validada | `(tenant_id, job_id)` → `core.jobs(tenant_id, id)`, `ON DELETE CASCADE`, validada |
| Índices (por partición) | `idx_usage_tokens_tenant_ticket_id`, `usage_tokens_tenant_ix`, `usage_tokens_tenant_ticket_etapa_ix` | `idx_job_transitions_tenant_job_id`, `job_transitions_tenant_job_ix` |
| Otros | CHECK `ut_pos_chk`, `ut_tier_chk`; RLS; permisos S/I/U/D para la app | RLS; permisos S/I; trigger de solo-añadir (`025`) |
| Autovacuum | `scale_factor 0,02`, `insert_scale_factor 0,02`, `analyze_scale_factor 0,01` | `0,05` / `0,05` / `0,02` |

Las claves ajenas, los CHECK, la FK a `core.organizations`, la política RLS y los permisos se reproducen
idénticos; el `UNIQUE (tenant_id, id)` de las tablas padre (`012`) no cambia. Se verificó con
`test_las_claves_ajenas_compuestas_siguen_declaradas_y_validadas` (padre y cada partición) y con una
prueba que comprueba que la FK sigue rechazando un padre de otro tenant.

## 2. Poda de particiones: cómo escribir las consultas

PostgreSQL solo descarta particiones si la consulta filtra por `creado_en`. Las dos consultas que justifican
el particionado, con la forma que poda (comprobada con `EXPLAIN` sobre datos de tres meses en
`test_particion_consumo.py`):

```sql
-- Consumo de un ciclo (un mes): toca UNA partición
SELECT sum(tokens_in + tokens_out) FROM core.usage_tokens
 WHERE tenant_id = :t AND creado_en >= :inicio_ciclo AND creado_en < :fin_ciclo;

-- Historial de un job: toca solo las particiones de la vida del job
SELECT de_estado, a_estado, creado_en FROM core.job_transitions
 WHERE tenant_id = :t AND job_id = :j AND creado_en >= :desde AND creado_en < :hasta
 ORDER BY creado_en;
```

**Ojo:** algunas consultas de la API siguen sin acotar por fecha (el consumo por ticket de `routers/tickets.py`
y `routers/usage.py` `/by-ticket`, ambas por `ticket_id`). Siguen funcionando (el índice por partición resuelve cada una),
pero recorren las particiones; con muchos meses conviene añadirles `creado_en >= <fecha de creación del
ticket>`. No se tocó la API en este ticket (es de Backend); queda anotado.

## 3. Autovacuum (§9.3)

Los parámetros de almacenamiento **no se pueden fijar en una tabla particionada**: se fijan en cada
partición. `pg_partman` copia a cada partición nueva los de su tabla plantilla
(`partman.template_core_usage_tokens`, `partman.template_core_job_transitions`), de modo que la migración
los fija en la plantilla y en las particiones existentes, y las de los meses futuros los heredan
(`test_los_parametros_de_autovacuum_...`).

- **Por qué más estrictos que los de PostgreSQL (20 % / 10 %):** son tablas de inserción continua que se
  leen justo después de escribirse. Con el 20 % por defecto el autovacuum de la partición caliente espera
  a acumular una quinta parte de filas nuevas, y hasta entonces el mapa de visibilidad (index-only scans),
  las estadísticas y el congelado de filas quedan atrasados.
- **`usage_tokens` más estricta que `job_transitions`:** rota más (varias filas por paso de cada ticket).
- **Particiones antiguas:** dejan de recibir escrituras y el autovacuum las deja en paz una vez congeladas.
- **Para cambiarlos:** editar la migración nueva que corresponda y repetir el `ALTER TABLE ... SET (...)` en
  la plantilla y en cada partición; no se configuran en el servidor.

> El ticket pide «subir el umbral de autovacuum en `usage_tokens`». Se interpretó como hacer que el
> autovacuum actúe **antes** (reducir los factores de escala); si se quería lo contrario (menos
> frecuente), basta con cambiar los valores en la sección 3 de la migración.

## 4. Seguridad: las particiones no deben ser accesibles para la aplicación

**Hallazgo.** `db/04_rls.sql` concede por defecto `SELECT, INSERT, UPDATE, DELETE` a `kallicode_app` sobre
**toda tabla nueva de `core`** creada por `kallicode_admin`. Una partición es una tabla nueva: nace con
esos permisos. Pero la política RLS (`tenant_isolation`) vive en la tabla **padre**; las particiones no la
heredan (ni `pg_partman` la copia desde la plantilla). Quien tuviera permiso sobre una partición la leería
**entera, de todos los tenants**, saltándose el aislamiento. En `audit.audit_events` (KC-DB-07-1) no ocurría
porque el esquema `audit` no tiene permisos por defecto.

**Solución.**

- `db/00_extensiones.sql` (superusuario) crea el *event trigger* `kc_particiones_sin_acceso`: cada vez
  que se crea o se adjunta una tabla que es partición en `core` o `vec`, le quita **todo** acceso a
  `kallicode_app` y `kallicode_worker`. Cubre también las particiones que `pg_partman_bgw` crea cada mes.
- La migración `035` se **niega a aplicarse** si el event trigger no existe, y al terminar comprueba que
  ninguna partición tenga permisos para la aplicación.
- La aplicación sigue accediendo con normalidad a través del padre, que lleva la política.
- Pruebas: `test_la_aplicacion_no_tiene_acceso_a_las_particiones` (existentes y la que crea el
  mantenimiento) y `test_el_event_trigger_de_particiones_esta_instalado`.

Si un servidor gestionado no permitiera crear *event triggers*, no se podrían particionar tablas con RLS
en `core` sin otro mecanismo equivalente: la migración lo dice y no aplica nada.

## 5. V21 y las particiones

`core.incoherencias_tenant()` (V21, `013`) recorre todas las claves ajenas de `pg_constraint`. Una tabla
particionada reproduce cada FK en cada partición (`conparentid <> 0`); la función las habría seguido con
un `SELECT` directo sobre cada partición, y al ejecutarla la aplicación habría fallado con *permission
denied*. La migración la redefine con una sola condición añadida (`c.conparentid = 0`): la FK del padre ya
cubre todas las filas. Prueba: `test_v21_ignora_las_fk_clonadas_en_las_particiones`.

## 6. Aplicar la migración

Mismos requisitos que KC-DB-07-1 (imagen con `pg_partman`, `00_extensiones.sql` como superusuario, que
ahora también instala el event trigger). Durante la copia cada tabla está bloqueada para escritura
(`lock_timeout = 30 s`). La copia va mes a mes y se verifica (filas, ids y una huella del contenido);
si algo no cuadra, se revierte entera. Es idempotente.

```bash
psql -U postgres -d <base> -f db/00_extensiones.sql      # extensión, permisos y event trigger
uv run python packages/core/migrate.py                   # aplica la 035
```

## 7. Decisiones y desviaciones respecto del ticket

- **Número de migración:** el ticket nombra `025_particion_consumo.sql`; la 025 ya existe (append-only de
  las probatorias) y la 034 la tomó `retention_policies`. Es la `035`.
- **Retención:** no se aplica; la gobernará `core.retention_policies` (KC-DB-07-4).
- **Reversión:** no se pidió ni se escribió un script propio para estas tablas; sirve de modelo
  `db/tools/revertir_particion_auditoria.sql`.
- **Cambios fuera de las dos tablas** (necesarios para que el particionado no rompa nada):
  `core.incoherencias_tenant()` (V21), `db/00_extensiones.sql` (event trigger) y
  `test_convencion_indices.py` (resuelve el índice de cada partición a su índice padre).
