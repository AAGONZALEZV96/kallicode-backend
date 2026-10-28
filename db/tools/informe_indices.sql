-- ============================================================================
-- KALLICODE · db/tools/informe_indices.sql
-- KC-DB-06-1 — Informe de índices y consultas costosas del entorno de demostración.
--
-- Uso (superusuario o rol con pg_read_all_stats; el plan se captura como
-- kallicode_app para que incluya las políticas RLS reales):
--
--   psql -d <base> -v ON_ERROR_STOP=1 -v tenant_plan=org_demo -f db/tools/informe_indices.sql
--
-- Lee pg_stat_user_indexes (siempre disponible) y pg_stat_statements (solo si la
-- extensión está creada y cargada con shared_preload_libraries; si no, esas
-- secciones se omiten y el informe lo dice). No modifica nada: solo consulta, y
-- crea una tabla temporal para guardar los planes.
--
-- Lo que mide es la CARGA QUE HAYA HABIDO desde el último reset de estadísticas
-- (pg_stat_reset / pg_stat_statements_reset). En el CI nocturno esa carga son los
-- tests de la API y de integración; con tablas de demostración pequeñas el
-- planificador prefiere recorrerlas enteras, así que un índice sin escaneos aquí
-- NO es evidencia de que sobre: es solo un candidato a revisar (ver
-- docs/plan-indices.md).
-- ============================================================================
\set QUIET on
\pset pager off
\pset null '-'
\pset format aligned
\pset linestyle unicode
\if :{?tenant_plan}
\else
\set tenant_plan org_demo
\endif

SELECT (EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
        AND current_setting('shared_preload_libraries') LIKE '%pg_stat_statements%') AS hay_pss \gset

\echo
\echo '=== 0. Contexto de la medición ==='
SELECT now()::timestamp(0)                                         AS generado_en,
       current_database()                                          AS base,
       split_part(version(), ' ', 2)                               AS postgres,
       (SELECT stats_reset::timestamp(0) FROM pg_stat_database
         WHERE datname = current_database())                       AS contadores_desde,
       :'hay_pss'::boolean                                         AS pg_stat_statements;

\echo
\echo '=== 1. Índices explícitos y su uso (excluye los que respaldan PK/UNIQUE) ==='
-- Los índices que respaldan una restricción no se pueden retirar por separado: se
-- justifican por la restricción y no entran en este plan.
-- Un índice de tabla particionada (audit.audit_events, KC-DB-07-1) existe una vez por
-- partición: se informa como UNO solo, con los escaneos y el tamaño de todas sus
-- particiones sumados (pg_partition_root devuelve el índice declarado en la tabla padre).
CREATE TEMP VIEW uso_indices AS
SELECT coalesce(pg_partition_root(s.indexrelid), s.indexrelid::regclass)::oid AS raiz,
       s.schemaname,
       sum(s.idx_scan)::bigint                                      AS escaneos,
       sum(s.idx_tup_read)::bigint                                  AS tuplas_leidas,
       sum(pg_relation_size(s.indexrelid))                          AS bytes,
       bool_or(i.indisunique)                                       AS es_unico
  FROM pg_stat_user_indexes s
  JOIN pg_index i ON i.indexrelid = s.indexrelid
 WHERE s.schemaname IN ('core', 'vec', 'audit', 'cm')
   AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = s.indexrelid)
 GROUP BY 1, 2;

SELECT u.schemaname || '.' || r.relname                             AS indice,
       t.relname                                                    AS tabla,
       pg_size_pretty(u.bytes)                                      AS tamano,
       u.escaneos,
       u.tuplas_leidas,
       CASE WHEN u.es_unico THEN 'unico' ELSE 'acceso' END          AS tipo
  FROM uso_indices u
  JOIN pg_class r ON r.oid = u.raiz
  JOIN pg_index ri ON ri.indexrelid = u.raiz
  JOIN pg_class t ON t.oid = ri.indrelid
 ORDER BY u.escaneos DESC, u.schemaname, r.relname;

\echo
\echo '=== 2. Candidatos a retiro: índices de acceso sin ningún escaneo en la medición ==='
-- Solo índices de acceso (no únicos): los únicos protegen integridad aunque nadie los escanee.
SELECT u.schemaname || '.' || r.relname                             AS indice,
       t.relname                                                    AS tabla,
       pg_size_pretty(u.bytes)                                      AS tamano
  FROM uso_indices u
  JOIN pg_class r ON r.oid = u.raiz
  JOIN pg_index ri ON ri.indexrelid = u.raiz
  JOIN pg_class t ON t.oid = ri.indrelid
 WHERE u.escaneos = 0
   AND NOT u.es_unico
 ORDER BY u.bytes DESC, 1;

\echo
\echo '=== 3. Claves foráneas sin un índice que las cubra (índices que faltan) ==='
-- Una FK cuyas columnas no son el prefijo de ningún índice de la tabla hija obliga a
-- recorrerla en cada borrado o validación del padre.
SELECT c.conrelid::regclass::text                                   AS tabla_hija,
       c.conname                                                    AS restriccion,
       (SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
          FROM unnest(c.conkey) WITH ORDINALITY k(n, ord)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n) AS columnas
  FROM pg_constraint c
 WHERE c.contype = 'f'
   AND c.connamespace::regnamespace::text IN ('core', 'vec', 'audit', 'cm')
   AND NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indrelid = c.conrelid
           AND i.indisvalid
           AND i.indpred IS NULL                -- un índice parcial no cubre la FK
           AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] @> c.conkey
           AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] <@ c.conkey)
 ORDER BY 1, 2;

\if :hay_pss
\echo
\echo '=== 4. Las 15 consultas más costosas de la aplicación (pg_stat_statements) ==='
-- Solo sentencias de nivel superior ejecutadas por kallicode_app (excluye lo que
-- disparan los triggers y las herramientas de administración). p95 propuesto =
-- media + 2 desviaciones, redondeado hacia arriba a múltiplos de 5 ms (mínimo
-- 5 ms): pg_stat_statements no entrega percentiles, así que es una aproximación
-- inicial que debe validarse con carga real.
DROP TABLE IF EXISTS pg_temp.top15;
CREATE TEMP TABLE top15 AS
SELECT row_number() OVER (ORDER BY s.total_exec_time DESC)           AS n,
       s.queryid,
       s.calls,
       round(s.total_exec_time::numeric, 1)                          AS total_ms,
       round(s.mean_exec_time::numeric, 3)                           AS media_ms,
       round(s.stddev_exec_time::numeric, 3)                         AS desv_ms,
       round(s.max_exec_time::numeric, 2)                            AS max_ms,
       s.rows,
       s.shared_blks_hit,
       s.shared_blks_read,
       greatest(5, ceil((s.mean_exec_time + 2 * s.stddev_exec_time) / 5) * 5)::int AS p95_propuesto_ms,
       regexp_replace(s.query, '\s+', ' ', 'g')                      AS consulta
  FROM pg_stat_statements s
  JOIN pg_roles r ON r.oid = s.userid
 WHERE r.rolname = 'kallicode_app'
   AND s.toplevel
   AND s.query ~* '^\s*(select|with|insert|update|delete)'
   AND s.query !~* 'set_config|pg_catalog\.version|pg_stat_statements'
 ORDER BY s.total_exec_time DESC
 LIMIT 15;

SELECT n, calls AS llamadas, total_ms, media_ms, desv_ms, max_ms,
       p95_propuesto_ms AS "p95_propuesto_ms", left(consulta, 110) AS consulta
  FROM pg_temp.top15 ORDER BY n;

\echo
\echo '=== 5. Plan de ejecución genérico de cada una (EXPLAIN GENERIC_PLAN como kallicode_app) ==='
DROP TABLE IF EXISTS pg_temp.top15_planes;
CREATE TEMP TABLE top15_planes (n int, linea_no int, linea text);
GRANT ALL ON pg_temp.top15_planes TO PUBLIC;
GRANT SELECT ON pg_temp.top15 TO PUBLIC;
SELECT set_config('informe.tenant_plan', :'tenant_plan', false);
DO $plan$
DECLARE
    r record;
    l text;
    i int;
BEGIN
    FOR r IN SELECT n, consulta FROM pg_temp.top15 ORDER BY n LOOP
        i := 0;
        BEGIN
            -- Como la aplicación: sin esto el superusuario ignora RLS y el plan no
            -- incluiría el filtro por tenant. Se repite en cada iteración porque un
            -- error revierte la subtransacción y con ella el cambio de rol.
            SET LOCAL ROLE kallicode_app;
            PERFORM set_config('app.tenant_id', current_setting('informe.tenant_plan'), true);
            FOR l IN EXECUTE 'EXPLAIN (GENERIC_PLAN, COSTS OFF) ' || r.consulta LOOP
                i := i + 1;
                INSERT INTO pg_temp.top15_planes VALUES (r.n, i, l);
            END LOOP;
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO pg_temp.top15_planes
            VALUES (r.n, 1, '(no se pudo capturar el plan genérico: ' || SQLERRM || ')');
        END;
    END LOOP;
    RESET ROLE;
END
$plan$;
SELECT n, linea_no AS linea, linea AS plan FROM pg_temp.top15_planes ORDER BY n, linea_no;
\else
\echo
\echo '=== 4-5. Consultas costosas y planes: OMITIDAS ==='
\echo 'pg_stat_statements no está creada o cargada. Actívala con'
\echo '  shared_preload_libraries = pg_stat_statements   (reinicio de Postgres)'
\echo '  CREATE EXTENSION pg_stat_statements;'
\endif

\echo
\echo '=== Fin del informe ==='
