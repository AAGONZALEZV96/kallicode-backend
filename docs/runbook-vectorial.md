# Runbook de la colección vectorial: construcción y reindexado concurrentes de HNSW (KC-DB-08-2, DB-F10)

Procedimiento operativo para construir, reconstruir y reindexar los tres índices HNSW de `vec.*` **sin
bloquear escrituras**. El §9.2 exigía `CONCURRENTLY` y `maintenance_work_mem >= 2 GB` en producción, pero
no había procedimiento: el script que creaba los índices (`db/02_indexes.sql`) corre dentro de una
transacción y bloquea las escrituras mientras se construye.

| Índice | Tabla | Particionada |
|---|---|---|
| `ticket_embeddings_hnsw_ix` | `vec.ticket_embeddings` | no |
| `doc_chunks_hnsw_ix` | `vec.doc_chunks` | sí (LIST por tenant, 037) |
| `definition_embeddings_hnsw_ix` | `vec.definition_embeddings` | sí (LIST por tenant, 037) |

Los tres: `hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)` sobre `vector(1024)` (ADR-022: se
mantiene float4; el script lee la clase de operadores de la columna `opclase` del catálogo, migración `045`, así que un
cambio a halfvec no exige tocarlo; el procedimiento para cambiar está en el ADR-022 y en `db/propuestas/halfvec/`). Su definición está en el
catálogo `vec.hnsw_catalogo` (migración `044_hnsw_operaciones.sql`), que lee el script.

## 1. Cuándo usar este runbook

| Situación | Qué hacer |
|---|---|
| Base nueva, o un índice HNSW no existe | Sección 3: construcción |
| Una construcción se interrumpió (cancelada, caída del servidor, timeout) | Sección 5: recuperación (es la misma ejecución) |
| La hinchazón de un índice crece (sección 4.3) | Sección 4: REINDEX (a mano o ya programado) |
| Cambiar `m` o `ef_construction` | Editar `vec.hnsw_catalogo` y `db/02_indexes.sql`, borrar el índice y ejecutar la sección 3 |

## 2. Requisitos

- **`maintenance_work_mem >= 2 GB`** en la sesión que construye (§9.2). Con menos, el grafo no cabe en memoria
  y pgvector lo construye en disco (mucho más lento: lo avisa con `NOTICE: hnsw graph no longer fits into
  maintenance_work_mem`). El rol `kallicode_admin` lo trae por defecto (`ALTER ROLE ... SET` en
  `db/00_roles.sql`, que ejecuta un superusuario); el script lo vuelve a fijar con `-v mwm=2GB` y **se niega a
  seguir con menos** (salvo `-v permitir_poco_mwm=1`, solo para pruebas). El valor efectivo queda registrado.
- **Memoria del servidor** para `maintenance_work_mem` más lo que ya consuma (cada construcción o REINDEX usa
  hasta ese valor: no coincidan dos a la vez, ver el escalonado de la sección 4).
- **Espacio en disco**: `CREATE INDEX CONCURRENTLY` y `REINDEX CONCURRENTLY` construyen el índice nuevo **junto
  al existente**. Cuente el tamaño del índice, una vez más, y WAL (`max_wal_size` alto o checkpoints frecuentes).
  Tamaño medido: ~8.300 bytes de índice por fila (vectores de 1024 dimensiones).
- Rol `kallicode_admin` (dueño de las tablas). **Fuera de una transacción**: `psql -f` en modo normal.
- pgvector >= 0.8 (CONCURRENTLY y escaneo iterativo).

## 3. Construcción concurrente

```bash
psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/06_indices_concurrentes.sql
```

Variables opcionales: `-v mwm=2GB`, `-v workers=4` (`max_parallel_maintenance_workers`),
`-v solo=<indice>` (uno solo), `-v permitir_poco_mwm=1` (pruebas).

Qué hace con cada índice del catálogo:

1. Elimina (`DROP INDEX CONCURRENTLY`) los **restos inválidos** de una construcción interrumpida.
2. **Tabla normal** (`ticket_embeddings`): `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.
3. **Tabla particionada** (`doc_chunks`, `definition_embeddings`): PostgreSQL **no admite** `CREATE INDEX
   CONCURRENTLY` sobre la tabla padre (`cannot create index on partitioned table ... concurrently`). El script
   crea el índice del padre `ON ONLY` (instantáneo, queda inválido), construye uno por partición con
   `CONCURRENTLY` y lo adjunta con `ALTER INDEX ... ATTACH PARTITION`; al adjuntar el último, el del padre pasa
   a válido. Una partición nueva de tenant (LIST) **necesita su índice**: ejecute el script tras crearla.
4. Registra la duración de lo construido en `vec.hnsw_metricas` (`evento = 'construccion'`) con el
   `maintenance_work_mem` efectivo.

Es **idempotente**: con los tres índices completos y válidos no hace nada (`Índices construidos en esta
ejecución: 0`). Antes de las migraciones (cuando `vec.hnsw_catalogo` no existe) tampoco hace nada, así que
puede vivir en `db/` sin alterar el orden `01..05` del esquema basal.

**Verificación** (debe devolver `valido = t`, `sin_indice = 0`, `invalidos = 0` en las tres filas):

```sql
SELECT indice, existe, valido, particiones_sin_indice AS sin_indice, indices_invalidos AS invalidos, tamano
  FROM vec.v_hnsw_estado ORDER BY indice;
```

**Durante la construcción** las escrituras no se bloquean: solo esperan un instante en las dos esperas de
snapshots de `CONCURRENTLY`. Las filas que se escriben mientras tanto se incorporan al índice. Qué se midió:
sección 6.

## 4. REINDEX CONCURRENTLY programado con pg_cron

Los HNSW se degradan con `UPDATE` y `DELETE`: dejan tuplas muertas en el grafo que `VACUUM` marca pero **no
compacta**; solo un `REINDEX` reconstruye el grafo. La migración `044` programa, cada **domingo (UTC)**, un
trabajo de `pg_cron` por paso (`REINDEX CONCURRENTLY` no puede ir dentro de una función ni de un bloque de
varias sentencias, así que cada paso es un trabajo de una sola sentencia):

| Trabajo | Cuándo (UTC) | Orden |
|---|---|---|
| `hnsw_medir_antes` | dom 02:00 | `SELECT vec.medir_hnsw('antes')` |
| `hnsw_reindex_ticket_embeddings_hnsw_ix` | dom 02:15 | `REINDEX INDEX CONCURRENTLY vec.ticket_embeddings_hnsw_ix` |
| `hnsw_reindex_doc_chunks_hnsw_ix` | dom 03:15 | `REINDEX INDEX CONCURRENTLY vec.doc_chunks_hnsw_ix` |
| `hnsw_reindex_definition_embeddings_hnsw_ix` | dom 04:15 | `REINDEX INDEX CONCURRENTLY vec.definition_embeddings_hnsw_ix` |
| `hnsw_medir_despues` | dom 06:00 | `SELECT vec.medir_hnsw('despues')` |

Corren como `kallicode_admin` (con `maintenance_work_mem = 2 GB` por rol). `REINDEX INDEX CONCURRENTLY`
sobre un índice de tabla particionada reindexa cada partición, una por una. Si una orden falla deja un índice
`..._ccnew` inválido: se retira ejecutando el script de la sección 3 (paso 1).

### 4.1 Requisitos de pg_cron

`shared_preload_libraries = pg_cron` y `cron.database_name = <base>` (`docker-compose.yml`; en Azure, permitir
`pg_cron` en `azure.extensions`), más `CREATE EXTENSION pg_cron` y `GRANT USAGE ON SCHEMA cron` a
`kallicode_admin` (ambos en `db/00_extensiones.sql`, superusuario). **Sin pg_cron** la migración `044` lo avisa y
no programa nada: ejecute la sección 4.2 a mano o desde el planificador que tenga.

### 4.2 Ajustar el escalonado al volumen

Cada REINDEX usa `maintenance_work_mem` (2 GB): **no deben coincidir**. Los 15 minutos de margen entre los
trabajos son para volúmenes pequeños; con la ventana por millón de filas de la sección 7, reprograme:

```sql
-- p. ej. con 3 M de filas en doc_chunks: dejarle 2 h antes del siguiente
SELECT cron.alter_job((SELECT jobid FROM cron.job WHERE jobname = 'hnsw_reindex_doc_chunks_hnsw_ix'),
                      schedule := '15 3 * * 0');
SELECT cron.alter_job((SELECT jobid FROM cron.job WHERE jobname = 'hnsw_reindex_definition_embeddings_hnsw_ix'),
                      schedule := '15 5 * * 0');
SELECT cron.alter_job((SELECT jobid FROM cron.job WHERE jobname = 'hnsw_medir_despues'), schedule := '0 8 * * 0');
```

A mano (sin pg_cron), como `kallicode_admin`, **sin transacción**: `SELECT * FROM vec.medir_hnsw('antes');`,
después cada `REINDEX INDEX CONCURRENTLY vec.<indice>;` y por último `SELECT * FROM vec.medir_hnsw('despues', <segundos>);`.

### 4.3 Métrica de hinchazón

`vec.medir_hnsw(evento)` guarda en `vec.hnsw_metricas`, **antes y después** de cada REINDEX y tras cada
construcción: tamaño del índice (suma de sus particiones), filas vivas y muertas, bytes por fila viva,
`hinchazon_pct`, duración y `maintenance_work_mem` efectivo. `pgstattuple` **no soporta** el método de acceso
hnsw, así que la hinchazón es un **indicador**: `hinchazon_pct` = bytes de índice por fila viva frente al
**mejor valor registrado** tras una construcción o un REINDEX. Crece con los `UPDATE`/`DELETE` y vuelve a ~0
tras reindexar. La duración de cada REINDEX programado se toma de `cron.job_run_details`.

```sql
SELECT creado_en::date AS dia, indice, evento, pg_size_pretty(tamano_bytes) AS tamano, filas_vivas,
       filas_muertas, bytes_por_fila, hinchazon_pct, duracion_s, maintenance_work_mem
  FROM vec.hnsw_metricas WHERE indice = 'doc_chunks_hnsw_ix' ORDER BY id DESC LIMIT 12;
```

Criterio sugerido: `hinchazon_pct > 30` tras una semana, o `filas_muertas` > 20 % de `filas_vivas`, pide
adelantar el REINDEX; si tras el REINDEX `hinchazon_pct` no baja de ~5, revisar si cambió el contenido (no
es hinchazón).

## 5. Recuperación de una construcción interrumpida

Una construcción concurrente interrumpida (cancelada, timeout, caída del servidor) **deja un índice inválido**:
ninguna consulta lo usa, pero se actualiza en cada escritura y ocupa espacio. Se detecta y se reconstruye así:

```sql
SELECT indice, valido, particiones_sin_indice, indices_invalidos FROM vec.v_hnsw_estado;
-- valido = f, o particiones_sin_indice > 0, o indices_invalidos > 0  =>  reconstruir
```

```bash
psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/06_indices_concurrentes.sql
```

El script retira los restos inválidos y continúa donde se quedó (en una tabla particionada, solo construye las
particiones que faltan). Lo cubre `packages/core/tests/integracion/test_hnsw_concurrente.py`, que interrumpe una
construcción real a mitad (tabla normal y particionada), comprueba que el índice queda inválido y se detecta,
y que el script lo reconstruye y el índice sirve a las consultas.

## 6. Ensayo con carga concurrente sobre la colección

**Qué se midió y cómo.** Colección de vectores aleatorios de 1024 dimensiones en `vec.doc_chunks` (tabla
particionada, partición por defecto), PostgreSQL 16 con pgvector 0.8 en Docker (12 CPU, 5,6 GB de RAM),
`maintenance_work_mem = 2 GB`, `max_parallel_maintenance_workers = 4`. El índice se construye con
`db/06_indices_concurrentes.sql` **mientras dos conexiones trabajan sin parar**: una escribe (un `INSERT` y un
`UPDATE` cada ~20 ms) y otra lee. Se registra la latencia de cada operación y los errores.

| Filas | Duración de la construcción | Filas/min | Índice | Escrituras durante la construcción | Lecturas | Errores |
|---|---|---|---|---|---|---|
| 52.643 | 235 s | 13.400 | 411 MB | 2.602 · p50 52 ms · p99 143 ms · máx 5,6 s | 4.502 · p50 1,8 ms · máx 11 ms | **0** |
| 213.946 | 990 s (16,5 min) | 13.000 | 1,78 GB | 13.860 · p50 17 ms · p99 280 ms · máx 12,7 s | 17.935 · p50 2,6 ms · máx 2,1 s | **0** |

- **No se bloquean las escrituras:** durante los 16,5 minutos de la construcción más grande se completaron 13.860
  escrituras y 17.935 lecturas **sin ningún error ni bloqueo sostenido**. Aparecen paradas puntuales de hasta
  ~12 s en una escritura (coinciden con la carga de disco de la construcción: una operación tarda, las
  siguientes no); el p99 es de ~0,3 s. Por eso el procedimiento se programa en horas valle.
- **Tamaño:** ~8.300 bytes de índice por fila (el índice guarda una copia de cada vector de 4 KB más el grafo).
- **Sobre 10⁶ filas (criterio del ticket): NO se completó en el equipo de pruebas.** Con 10⁶ filas cargadas
  (heap de 5,4 GB) la construcción alcanzó ~450.000 tuplas a ~24.000 tuplas/min (unos 20 min) y **después se
  hundió a 1.000–5.000 tuplas/min**: con 2 GB el grafo deja de caber en `maintenance_work_mem` y pgvector lo
  construye en disco (lo avisa con un `NOTICE`). Se interrumpió a las ~450.000–550.000 tuplas tras 30–50 min
  (en dos intentos; el primero se cortó por un reinicio del equipo y dejó el índice inválido: exactamente el
  caso de la sección 5, que el script recupera). Un equipo con 11,7 GB de RAM no admite el
  `maintenance_work_mem` que haría falta para 10⁶ filas (sección 7). **Falta repetir el ensayo en un servidor
  dimensionado** con `-v mwm=<valor de la sección 7>`; el procedimiento y el registro de métricas son los mismos.

## 7. Ventana necesaria por millón de filas

Con el equipo de pruebas medimos dos regímenes; **lo que sigue es una extrapolación**, no una medición a 10⁶:

| Régimen | Cuándo | Velocidad medida | Para 10⁶ filas |
|---|---|---|---|
| El grafo cabe en `maintenance_work_mem` | hasta ~450.000 filas con 2 GB (≈ **4,5 KB de `maintenance_work_mem` por fila**) | 13.000–24.000 filas/min | **45–80 min** |
| El grafo no cabe | más allá | 1.000–5.000 filas/min (5 a 20 veces más lento) | **horas (más de 3 h)**: no recomendado |

Regla para planificar la ventana:

1. **`maintenance_work_mem` ≈ 4,5 KB × filas** de la mayor partición o colección (≈ 4,5 GB para 10⁶; el
   mínimo del §9.2 son 2 GB, válidos hasta ~450.000 filas). Compruebe que el servidor tiene esa memoria libre.
2. **Ventana ≈ 80 min por millón de filas** (13.000 filas/min, con la carga habitual funcionando), más un
   margen del 50 %: **2 h por millón** si se reservan ventanas fijas.
3. **Espacio en disco:** ~8,3 GB de índice por millón de filas **además** del índice vigente, más el WAL.
4. Si la colección supera lo que cabe en memoria, es preferible aprovechar el particionado por tenant
   (`doc_chunks` y `definition_embeddings`, 037): el script construye **cada partición por separado**, así que
   cada una necesita memoria solo para sí misma.
5. El escalonado de pg_cron de la sección 4 (15 min entre índices) solo vale para colecciones pequeñas
   (menos de ~100.000 filas por índice): ajústelo con esta regla.

## 8. Resumen de lo que deja el ticket

- `db/06_indices_concurrentes.sql`: construcción concurrente, idempotente, con recuperación y registro.
- `db/migrations/044_hnsw_operaciones.sql`: `vec.hnsw_catalogo`, `vec.hnsw_metricas`, `vec.medir_hnsw()`,
  `vec.v_hnsw_estado` y los trabajos de pg_cron. `db/00_roles.sql`: `maintenance_work_mem = 2 GB` para `kallicode_admin`.
- `db/00_extensiones.sql` y `docker-compose*.yml`: pg_cron (`db/postgres/Dockerfile` la instala).
- `packages/core/tests/integracion/test_hnsw_concurrente.py` (CI): 16 pruebas.
