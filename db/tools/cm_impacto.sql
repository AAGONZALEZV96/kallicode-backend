-- ============================================================================
-- KALLICODE · db/tools/cm_impacto.sql
-- KC-DB-11-2 — Diagnóstico del impacto a k saltos: estado de cm.reachability,
-- planes de las dos rutas de cm.impacto() y diferencias entre ellas.
--
-- Uso (como kallicode_app, para que el plan incluya las políticas RLS reales):
--
--   psql -d <base> -v ON_ERROR_STOP=1 -v tenant=org_demo -v nodo=sym_x \
--        [-v k=3] [-v max_visitados=5000] [-v direccion=entrante] -f db/tools/cm_impacto.sql
--
-- No modifica nada: todo corre en una transacción que se revierte al final.
--   1. Estado de cm.reachability del tenant (vigente / obsoleta / excedida).
--   2. Resultado de cada ruta (materializada y recursiva) para el nodo pedido.
--   3. Diferencias entre las dos rutas (debe salir vacío si la tabla está vigente).
--   4. EXPLAIN (ANALYZE, BUFFERS) del cuerpo de cada ruta, con auto_explain si
--      está cargado; si no, el tiempo total de cada llamada.
-- ============================================================================
\set QUIET on
\pset pager off
\pset null '-'
\if :{?tenant}
\else
\echo 'ERROR: falta -v tenant=<tenant_id>'
\quit
\endif
\if :{?nodo}
\else
\echo 'ERROR: falta -v nodo=<id de cm.nodes>'
\quit
\endif
\if :{?k}
\else
\set k 3
\endif
\if :{?max_visitados}
\else
\set max_visitados 5000
\endif
\if :{?direccion}
\else
\set direccion entrante
\endif
\set QUIET off

BEGIN;
SELECT set_config('app.tenant_id', :'tenant', true);

\echo
\echo '== 1. Estado de cm.reachability'
SELECT e.estado, e.snapshot_id, sv.snapshot_id AS snapshot_vigente, e.filas, e.caminos,
       e.presupuesto, e.duracion_ms, e.refrescada_en, e.invalidada_en
  FROM cm.reachability_estado e
  LEFT JOIN cm.snapshot_vigente sv ON sv.tenant_id = e.tenant_id;

\echo
\echo '== 2. Resultado por ruta (resumen)'
SELECT r.fuente, count(*) AS nodos, max(r.distancia) AS distancia_max, bool_or(r.truncado) AS truncado
  FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                  :max_visitados, 'recursiva') r
 GROUP BY r.fuente
UNION ALL
SELECT r.fuente, count(*), max(r.distancia), bool_or(r.truncado)
  FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                  :max_visitados, 'auto') r
 GROUP BY r.fuente;

\echo
\echo '== 3. Diferencias entre la ruta automática y la recursiva (vacío = idénticas)'
WITH a AS (
    SELECT id, kind, distancia, via_tipo
      FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                      :max_visitados, 'auto')
), r AS (
    SELECT id, kind, distancia, via_tipo
      FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                      :max_visitados, 'recursiva')
)
(SELECT 'solo_auto' AS lado, * FROM (SELECT * FROM a EXCEPT SELECT * FROM r) x)
UNION ALL
(SELECT 'solo_recursiva', * FROM (SELECT * FROM r EXCEPT SELECT * FROM a) y)
ORDER BY 1, 2;

\echo
\echo '== 4. Planes (el cuerpo de una función plpgsql solo se ve con auto_explain.log_nested_statements)'
SET LOCAL auto_explain.log_min_duration = 0;
SET LOCAL auto_explain.log_analyze = on;
SET LOCAL auto_explain.log_buffers = on;
SET LOCAL auto_explain.log_nested_statements = on;
SET LOCAL client_min_messages = log;
\timing on
SELECT count(*) AS recursiva
  FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                  :max_visitados, 'recursiva');
SELECT count(*) AS auto
  FROM cm.impacto(ARRAY[:'nodo'], '{}', :k, cm.reachability_tipos(), :'direccion',
                  :max_visitados, 'auto');
\timing off

ROLLBACK;
