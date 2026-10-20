# Particionado de `audit.audit_events` (KC-DB-07-1, DB-F09)

`audit.audit_events` es la tabla que más crece del modelo y la única que no se puede truncar (es la
prueba). Desde la migración `db/migrations/033_particion_auditoria.sql` está particionada por rango
mensual de `creado_en` y la gestiona `pg_partman`. Este documento recoge cómo funciona, cómo se opera,
el ensayo con 5 millones de filas y el procedimiento de reversión.

## 1. Estado final

| Aspecto | Valor |
|---|---|
| Tabla | `audit.audit_events`, `PARTITION BY RANGE (creado_en)` |
| Clave primaria | `(creado_en, id)`, rediseñada en `017_pk_para_particion.sql` |
| Particiones | una por mes **en UTC** (`audit_events_p<AAAAMMDD>`), desde el mes de la fila más antigua |
| Pre-creadas | 12 meses futuros, más la del mes actual, más una partición `DEFAULT` |
| Gestión | `pg_partman` (`partman.part_config`): intervalo `1 month`, `premake = 12`, `infinite_time_partitions` |
| Retención | **ninguna**: la auditoría no se borra (`retention` queda en `NULL`) |
| Índices | los seis de auditoría, **por partición** (se declaran en el padre y PostgreSQL crea uno por partición) |
| Trigger de sellado, inmutabilidad, RLS, permisos, FK a `core.organizations` | idénticos a antes; `verify_chain`, V16 y V17 no cambian |

Los seis índices son `audit_tenant_fecha_ix`, `audit_ticket_ix`, `audit_actor_ix`, `audit_evento_ix`,
`audit_events_sello_ix` y `audit_events_request_id_ix`. El ticket habla de cuatro: son los de
`02_indexes.sql`; las migraciones `024` y `030` añadieron los otros dos.

## 2. Mantenimiento automático (la partición siguiente se crea sola)

`pg_partman` mantiene `premake` meses por delante cuando alguien ejecuta su mantenimiento. Lo hace su
worker en segundo plano, `pg_partman_bgw`, configurado en `docker-compose.yml` y
`docker-compose.test.yml`:

```
shared_preload_libraries = pg_stat_statements,pg_partman_bgw
pg_partman_bgw.interval  = 3600            # cada hora
pg_partman_bgw.dbname    = kallicode       # kallicode_test en el compose de pruebas
pg_partman_bgw.role      = kallicode_admin
```

En un servidor gestionado (Azure Database for PostgreSQL) el equivalente es permitir `pg_partman` en
`azure.extensions`, cargar `pg_partman_bgw` en `shared_preload_libraries` y fijar esos mismos parámetros,
o bien programar con `pg_cron`:

```sql
SELECT cron.schedule('particiones-auditoria', '0 3 * * *', $$CALL partman.run_maintenance_proc()$$);
```

Para comprobar que el mantenimiento funciona (debe haber al menos 12 meses por delante):

```sql
SELECT count(*) FILTER (WHERE lower_bound > date_trunc('month', now())) AS meses_por_delante
  FROM (SELECT (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'FROM \(''([^'']+)''\)'))[1]::timestamptz AS lower_bound
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = 'audit.audit_events'::regclass) p
 WHERE lower_bound IS NOT NULL;
```

Si el mantenimiento llegara a fallar durante más de `premake` meses, los eventos nuevos caerían en la
partición `DEFAULT` en vez de fallar. La prueba `test_la_particion_default_esta_vacia` lo detecta; si hay
filas en ella, créese la partición que falta con `CALL partman.partition_data_proc('audit.audit_events')`.

## 3. Operación diaria

- **Zona horaria.** Los límites se calculan en UTC (la migración fija `timezone = 'UTC'`). El servidor
  debe tener `timezone = 'UTC'` (valor por defecto de las imágenes y de Azure) para que el worker de
  mantenimiento calcule los mismos límites.
- **Consultas.** Filtrar por `creado_en` (`GET /audit/events` con fechas) permite que PostgreSQL toque solo
  las particiones implicadas (poda, comprobada en `test_particion_auditoria.py`). `audit.verify_chain`
  sin ventana recorre todas las particiones del tenant, igual que recorría toda la tabla antes.
- **Permisos.** La aplicación solo tiene `SELECT` e `INSERT` sobre el padre. Las particiones no reciben
  permisos ni llevan la política RLS: solo se llega a ellas por el padre, que sí la lleva. Una
  partición nueva no debe tener `GRANT` para `kallicode_app` (`inherit_privileges = false`).
- **Retención.** No hay. Si algún día se archiva, hacerlo con `DETACH PARTITION` sobre una partición
  completa y conservar la tabla desprendida: la cadena de sellos no admite huecos.

## 4. Aplicar la migración

Requisitos: una imagen o servidor con `pg_partman` (`db/postgres/Dockerfile` para desarrollo y CI), la
extensión creada por `db/00_extensiones.sql` (superusuario) y `kallicode_admin` con permisos sobre el
esquema `partman` (también lo hace `00_extensiones.sql`). Para actualizar un entorno existente:

```bash
docker compose build postgres && docker compose up -d postgres      # imagen con pg_partman
psql -U postgres -d <base> -f db/00_extensiones.sql                # crea la extensión y los permisos
uv run python packages/core/migrate.py                             # aplica la 033
```

La migración corre en una transacción y toma un bloqueo exclusivo de `audit.audit_events` mientras
copia las filas (con `lock_timeout = 30 s`: si no consigue el bloqueo, falla sin cambiar nada).
**Durante la copia la tabla no admite escrituras**: aplicarla en una ventana de bajo tráfico (ver el
ensayo, sección 5, para el tiempo esperado). Si cualquier verificación falla (filas, ids, sellos o filas
en la partición `DEFAULT`) se revierte entera.

## 5. Ensayo con cinco millones de filas

Se ensayó con `db/tools/ensayo_particion_auditoria.sql` sobre PostgreSQL 16 en Docker Desktop (Windows,
VM de 5,6 GB: un entorno lento a propósito, peor que cualquier servidor). La tabla simple tenía **5.002.006
filas** repartidas en 30 meses (3.150 MB con sus índices), de ellas 2.000 con una cadena de sellos íntegra
(`org_ensayo_cadena`), el resto sintéticas.

| Paso | Resultado |
|---|---|
| Carga de la tabla simple (solo ensayo) | 50 min (los checkpoints cada 5 s de la VM la hicieron lenta) |
| **Conversión con `033_particion_auditoria.sql`** | **1.021 s (≈ 17 min)**, con la tabla bloqueada para escritura |
| Particiones creadas | 42 con rango (de mayo de 2024 a octubre de 2027, 12 meses por delante) + `DEFAULT` |
| Filas en la partición `DEFAULT` | 0 |
| Filas, ids y sellos tras la conversión | idénticos (la migración lo verifica y se revierte si no) |
| `audit.verify_chain('org_ensayo_cadena')` | `integra = true`, 2.000 de 2.000 eventos |

Lectura: el tiempo es el de copiar 5 millones de filas y reconstruir 6 índices por partición; en un
servidor con disco normal será menor, pero conviene **planificar una ventana de mantenimiento**: durante
la copia no se puede escribir en la tabla. Con pocas filas (una base nueva o de desarrollo) la conversión tarda menos de un segundo.

**Worker de mantenimiento:** se comprobó con un servidor arrancado con la configuración del compose
(`pg_partman_bgw`, rol `kallicode_admin`, intervalo de 5 s para el ensayo): al subir `premake` en 2, el
worker creó las particiones que faltaban sin intervención y con `kallicode_admin` como dueño. Está
cubierto en `test_el_mantenimiento_crea_la_particion_siguiente_sin_intervencion`, que invoca el mismo
mantenimiento a mano. El rol del worker **debe** ser `kallicode_admin`: con otro rol las particiones
nuevas tendrían otro dueño (lo detecta `test_las_particiones_pertenecen_al_mismo_rol_que_el_padre`).

**Limitación del ensayo:** la reversión con 5 millones de filas **no se pudo medir**. Docker Desktop se
cayó tres veces durante esos intentos (la VM se quedó sin memoria con la copia en una sola sentencia; el
script y la migración se cambiaron después para copiar mes a mes). La reversión sí está probada, con la
misma lógica, en `test_la_reversion_conserva_las_filas_y_se_puede_volver_a_particionar` (filas, ids,
sellos, cadena íntegra y vuelta a particionar). Antes de usarla en un entorno con volumen, ensayarla en
una copia y medir su duración.

## 6. Reversión

`db/tools/revertir_particion_auditoria.sql` devuelve la tabla a su forma simple **conservando todas las
filas, también las escritas después de la migración**, con sus ids y sus sellos:

```bash
psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/tools/revertir_particion_auditoria.sql
```

Es una transacción única, idempotente y con la misma verificación (filas, ids, sellos). Retira a
`pg_partman` la gestión de la tabla y elimina la tabla particionada con sus particiones solo después de
verificar la copia. Para volver a particionar: `psql -f db/migrations/033_particion_auditoria.sql` (detecta
que la tabla no está particionada y la convierte). El ledger de migraciones no se modifica en ningún
sentido. Está probada en `test_la_reversion_conserva_las_filas_y_se_puede_volver_a_particionar` y en el
ensayo de la sección 5.

## 7. Decisiones y desviaciones respecto del ticket

- **Número de migración:** el ticket nombra `024_particion_auditoria.sql`; esa numeración ya está ocupada
  (`024_cadena_auditoria.sql`) y las migraciones aplicadas son inmutables. Es la `033`.
- **Imagen de PostgreSQL:** `pgvector/pgvector:pg16` no incluye `pg_partman`. Desarrollo y el informe
  nocturno usan `db/postgres/Dockerfile`; el servicio de PostgreSQL del job de CI instala el paquete
  `postgresql-16-partman` en un paso previo. Un servidor sin la extensión no puede aplicar la 033 y lo
  dice con un mensaje explícito.
- **Seis índices, no cuatro** (ver sección 1).
- **Tabla antigua:** la migración copia, verifica y **retira** la tabla antigua en la misma transacción
  (no deja una copia duplicada). La vuelta atrás es el script de la sección 6, no un `RENAME`.
- **Sin cambios en la suite de auditoría**, salvo una aserción: `test_auditoria_procedencia.py` comprobaba
  que el plan usara el índice llamado `audit_events_request_id_ix`; ahora cada partición tiene el suyo
  (`..._tenant_id_request_id_idx`, hijo de aquel) y la aserción acepta ese nombre.
- **`creado_en` lo fija el trigger de sellado** (`NEW.creado_en := now()`). Con la tabla particionada eso
  solo sigue siendo válido si el valor coincide con el que decidió la partición (el `DEFAULT now()` de la
  misma transacción): nadie inserta un `creado_en` distinto, y el trigger sigue sin dejar elegirlo.
- **`verify_chain` y `audit_events_sello_ix`:** el eslabón anterior se resuelve por `(tenant_id, sello)`
  sin `creado_en`, así que cada búsqueda consulta el índice de todas las particiones. Con las
  decenas de particiones previstas es aceptable; si creciera, habría que añadir `creado_en` a esa búsqueda.
