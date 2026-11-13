-- ============================================================================
-- KALLICODE · db/06_indices_concurrentes.sql
-- KC-DB-08-2 (DB-F10) — Construcción CONCURRENTE de los índices HNSW como procedimiento operativo.
--
-- 02_indexes.sql crea los HNSW dentro de la transacción del esquema basal: bloquea las escrituras y, con
-- una colección grande, durante mucho tiempo. Este script los construye (o los reconstruye si faltan o
-- quedaron INVÁLIDOS) con CREATE INDEX CONCURRENTLY, sin bloquear escrituras, y registra cuánto tardó y
-- con qué maintenance_work_mem en vec.hnsw_metricas. Es idempotente: si los tres índices están completos
-- y válidos, no hace nada. Procedimiento, ventana por millón de filas y recuperación:
-- docs/runbook-vectorial.md.
--
-- Uso (psql, como kallicode_admin; NO dentro de una transacción: CONCURRENTLY no lo admite):
--     psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/06_indices_concurrentes.sql
--
-- Variables (-v nombre=valor), todas opcionales:
--     mwm=2GB              maintenance_work_mem de la sesión. El §9.2 exige >= 2 GB: con menos el script
--                          se niega a seguir (salvo permitir_poco_mwm=1, solo para pruebas).
--     workers=4            max_parallel_maintenance_workers (construcción paralela de pgvector >= 0.6).
--     solo=<indice>        construye únicamente ese índice del catálogo (vec.hnsw_catalogo).
--     permitir_poco_mwm=1  permite un maintenance_work_mem menor de 2 GB (pruebas con pocas filas).
--
-- Qué hace con cada índice del catálogo:
--   1. Elimina (DROP INDEX CONCURRENTLY) los restos INVÁLIDOS de una construcción interrumpida
--      (cancelada, caída del servidor): no los usa ninguna consulta pero se actualizan en cada escritura.
--   2. Tabla normal: CREATE INDEX CONCURRENTLY IF NOT EXISTS.
--   3. Tabla particionada (vec.doc_chunks, vec.definition_embeddings): PostgreSQL NO admite CREATE INDEX
--      CONCURRENTLY sobre la tabla padre. Se crea el índice del padre ON ONLY (instantáneo; queda
--      inválido), se construye uno por partición con CONCURRENTLY y se adjunta (ATTACH PARTITION); al
--      adjuntar la última, el índice del padre pasa a válido.
--   4. Registra la duración de lo construido (vec.medir_hnsw('construccion', ...)).
--
-- Si se interrumpe, vuelva a ejecutarlo: retira los restos y continúa donde se quedó.
-- Antes de las migraciones (vec.hnsw_catalogo no existe todavía) no hace nada: así puede vivir en la
-- carpeta db/ sin alterar el orden 01..05 del esquema basal.
-- ============================================================================

\set ON_ERROR_STOP on
\set QUIET on

\if :{?mwm}
\else
    \set mwm 2GB
\endif
\if :{?workers}
\else
    \set workers 4
\endif
\if :{?solo}
\else
    \set solo ''
\endif
\if :{?permitir_poco_mwm}
\else
    \set permitir_poco_mwm 0
\endif

SELECT to_regclass('vec.hnsw_catalogo') IS NOT NULL AS hay_catalogo \gset
\if :hay_catalogo
\else
    \echo 'db/06_indices_concurrentes.sql: vec.hnsw_catalogo no existe todavía (aplique primero las migraciones); no se hace nada.'
    \quit
\endif

-- ---------------------------------------------------------------------------
-- 0. Parámetros de la sesión. Sin límite de tiempo: una construcción puede durar horas.
-- ---------------------------------------------------------------------------
SET statement_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET maintenance_work_mem = :'mwm';
SET max_parallel_maintenance_workers = :workers;

SELECT pg_size_bytes(current_setting('maintenance_work_mem')) >= pg_size_bytes('2GB') AS mwm_ok,
       current_setting('maintenance_work_mem') AS mwm_efectivo \gset
\if :mwm_ok
\else
    \if :permitir_poco_mwm
        \echo 'AVISO: maintenance_work_mem =' :mwm_efectivo '(< 2 GB, §9.2). Solo válido en pruebas.'
    \else
        \echo 'ERROR: maintenance_work_mem =' :mwm_efectivo '(< 2 GB, §9.2). Use -v mwm=2GB o más.'
        DO $$ BEGIN RAISE EXCEPTION 'maintenance_work_mem < 2 GB (§9.2); use -v mwm=2GB'; END $$;
    \endif
\endif

\echo
\echo '== Estado inicial de los HNSW =='
\echo 'maintenance_work_mem efectivo:' :mwm_efectivo ', max_parallel_maintenance_workers:' :workers
\set QUIET off
SELECT indice, existe, valido, particiones_sin_indice AS sin_indice, indices_invalidos AS invalidos, tamano
  FROM vec.v_hnsw_estado WHERE (:'solo' = '' OR indice = :'solo') ORDER BY indice;
\set QUIET on

-- ---------------------------------------------------------------------------
-- 1. Restos inválidos de una construcción interrumpida: índices HNSW inválidos que no están adjuntos a
--    un índice padre (los 'I' son índices de tablas particionadas: se completan en el paso 3).
-- ---------------------------------------------------------------------------
SELECT format('DROP INDEX CONCURRENTLY IF EXISTS %s', pc.oid::regclass) AS q
  FROM vec.hnsw_catalogo c
  JOIN LATERAL vec.hnsw_hojas(c.tabla::regclass) pt(relid) ON true
  JOIN pg_index pi ON pi.indrelid = pt.relid AND NOT pi.indisvalid
  JOIN pg_class pc ON pc.oid = pi.indexrelid AND pc.relkind = 'i' AND NOT pc.relispartition
  JOIN pg_am am ON am.oid = pc.relam AND am.amname = 'hnsw'
 WHERE c.activo AND (:'solo' = '' OR c.indice = :'solo')
 ORDER BY 1
\gexec

-- ---------------------------------------------------------------------------
-- 2 y 3. Construcción. Las marcas de tiempo (ini/fin) entre sentencias dan la duración de cada índice.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE IF NOT EXISTS _hnsw_marcas (indice text, punto text, t timestamptz);
TRUNCATE _hnsw_marcas;

WITH objetivos AS (
    SELECT c.indice, c.tabla::regclass AS tabla, c.columna, c.m, c.ef_construction,
           (SELECT relkind FROM pg_class WHERE oid = c.tabla::regclass) AS relkind,
           format('WITH (m = %s, ef_construction = %s)', c.m, c.ef_construction) AS opciones,
           split_part(c.tabla, '.', 1) AS esquema
      FROM vec.hnsw_catalogo c
     WHERE c.activo AND (:'solo' = '' OR c.indice = :'solo')
),
pasos AS (
    -- Tabla normal sin el índice (o con un resto que el paso 1 acaba de eliminar).
    SELECT o.indice, 20 AS orden,
           format('CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s USING hnsw (%I vector_cosine_ops) %s',
                  o.indice, o.tabla, o.columna, o.opciones) AS q
      FROM objetivos o
     WHERE o.relkind IN ('r', 'm')
       AND NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
                        WHERE i.indrelid = o.tabla AND ic.relname = o.indice AND i.indisvalid)
    UNION ALL
    -- Tabla particionada: índice del padre ON ONLY (no se puede CONCURRENTLY sobre el padre)...
    SELECT o.indice, 20,
           format('CREATE INDEX IF NOT EXISTS %I ON ONLY %s USING hnsw (%I vector_cosine_ops) %s',
                  o.indice, o.tabla, o.columna, o.opciones)
      FROM objetivos o
     WHERE o.relkind = 'p'
       AND to_regclass(o.esquema || '.' || o.indice) IS NULL
    UNION ALL
    -- ...un índice por partición hoja, con CONCURRENTLY...
    SELECT o.indice, 30 + row_number() OVER (PARTITION BY o.indice ORDER BY pt.relid::regclass::text) * 2,
           format('CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON %s USING hnsw (%I vector_cosine_ops) %s',
                  left(pc.relname, 50) || '_hnsw_ix', pt.relid::regclass, o.columna, o.opciones)
      FROM objetivos o
      JOIN LATERAL vec.hnsw_hojas(o.tabla) pt(relid) ON true
      JOIN pg_class pc ON pc.oid = pt.relid
     WHERE o.relkind = 'p'
       AND NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
                         JOIN pg_am am ON am.oid = ic.relam AND am.amname = 'hnsw'
                        WHERE i.indrelid = pt.relid AND i.indisvalid)
    UNION ALL
    -- ...y se adjunta cada uno al índice del padre.
    SELECT o.indice, 31 + row_number() OVER (PARTITION BY o.indice ORDER BY pt.relid::regclass::text) * 2,
           format('ALTER INDEX %s ATTACH PARTITION %s',
                  o.esquema || '.' || quote_ident(o.indice),
                  (SELECT pc.relnamespace::regnamespace::text || '.' || quote_ident(left(pc.relname, 50) || '_hnsw_ix')
                     FROM pg_class pc WHERE pc.oid = pt.relid))
      FROM objetivos o
      JOIN LATERAL vec.hnsw_hojas(o.tabla) pt(relid) ON true
     WHERE o.relkind = 'p'
       AND NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
                         JOIN pg_am am ON am.oid = ic.relam AND am.amname = 'hnsw'
                        WHERE i.indrelid = pt.relid AND i.indisvalid)
),
con_marcas AS (
    SELECT indice, orden, q FROM pasos
    UNION ALL SELECT DISTINCT indice, 0,  format('INSERT INTO _hnsw_marcas VALUES (%L, ''ini'', clock_timestamp())', indice) FROM pasos
    UNION ALL SELECT DISTINCT indice, 99999, format('INSERT INTO _hnsw_marcas VALUES (%L, ''fin'', clock_timestamp())', indice) FROM pasos
)
SELECT q FROM con_marcas ORDER BY indice, orden
\gexec

-- ---------------------------------------------------------------------------
-- 4. Registro de la duración de lo construido y estado final.
-- ---------------------------------------------------------------------------
SELECT count(DISTINCT indice) AS construidos FROM _hnsw_marcas \gset
-- Una fila de vec.hnsw_metricas por índice construido, con su duración medida entre las marcas.
SELECT count(r.id) AS registrados
  FROM (SELECT indice,
               extract(epoch FROM max(t) FILTER (WHERE punto = 'fin') - min(t) FILTER (WHERE punto = 'ini')) AS dur
          FROM _hnsw_marcas GROUP BY indice) m
  CROSS JOIN LATERAL vec.medir_hnsw('construccion', m.dur::numeric, m.indice) r \gset

\set QUIET off
\echo
\echo '== Resultado =='
\echo 'Índices construidos en esta ejecución:' :construidos
SELECT indice, evento, tamano_bytes, filas_vivas, duracion_s, maintenance_work_mem, particiones
  FROM vec.hnsw_metricas
 WHERE creado_en >= now() - interval '1 day' AND evento = 'construccion'
   AND (:'solo' = '' OR indice = :'solo')
 ORDER BY id DESC LIMIT 6;
SELECT indice, existe, valido, particiones_sin_indice AS sin_indice, indices_invalidos AS invalidos, tamano
  FROM vec.v_hnsw_estado WHERE (:'solo' = '' OR indice = :'solo') ORDER BY indice;
