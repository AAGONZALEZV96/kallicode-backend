-- ============================================================================
-- KALLICODE · db/tools/informe_retencion.sql
-- KC-DB-10-4 — Informe de cumplimiento de retención por tabla (evidencia para auditoría).
--
-- Uso (kallicode_admin o superusuario):
--
--   psql -d <base> -v ON_ERROR_STOP=1 [-v tolerancia='7 days'] -f db/tools/informe_retencion.sql
--
-- La consulta versionada es core.reporte_cumplimiento_retencion() (db/migrations/057_informe_retencion.sql); este
-- script solo la presenta por secciones. NO modifica nada: no ejecuta purgas ni exports. Lo publican como artefacto
-- el CI de cada PR (job database), el workflow nocturno retencion_nocturno.yml y, por workflow_call, el smoke
-- post-deploy. Cómo leerlo: docs/cumplimiento-retencion.md.
-- ============================================================================
\set QUIET on
\pset pager off
\pset null '-'
\pset format aligned
\pset linestyle unicode
\if :{?tolerancia}
\else
\set tolerancia '7 days'
\endif

CREATE TEMP TABLE informe AS
SELECT * FROM core.reporte_cumplimiento_retencion(now(), :'tolerancia'::interval);

\echo
\echo '=== 0. Contexto ==='
SELECT now()::timestamp(0)                       AS generado_en_utc,
       current_database()                        AS base,
       split_part(version(), ' ', 2)             AS postgres,
       :'tolerancia'::interval                   AS tolerancia,
       (SELECT max(m.archivo) FROM core.schema_migrations m) AS ultima_migracion,
       (SELECT count(*) FROM informe WHERE tenant IS NULL)  AS tablas,
       (SELECT count(*) FROM informe WHERE tenant IS NOT NULL) AS politicas_tenant;

\echo
\echo '=== 1. Resumen por estado ==='
SELECT estado, count(*) AS filas, bool_or(desviacion) AS desviacion, bool_or(alerta) AS alerta
  FROM informe GROUP BY estado ORDER BY desviacion DESC, estado;

\echo
\echo '=== 2. Tablas con plazo (purga por motor o dedicada) ==='
SELECT tabla, coalesce(tenant, '(plataforma)') AS ambito, accion, retencion, columna_tiempo,
       dato_mas_antiguo::timestamp(0) AS dato_mas_antiguo, referencia::timestamp(0) AS referencia,
       justify_interval(date_trunc('hour', antiguedad)) AS antiguedad,
       justify_interval(date_trunc('hour', exceso)) AS exceso,
       ultima_purga::timestamp(0) AS ultima_purga, filas_ultima_purga, estado
  FROM informe
 WHERE retencion IS NOT NULL OR estado IN ('no_medible', 'ignorada')
 ORDER BY tabla, tenant NULLS FIRST;

\echo
\echo '=== 3. Desviaciones y alertas (deben estar vacías) ==='
SELECT tabla, coalesce(tenant, '(plataforma)') AS ambito, estado, alerta, justificacion
  FROM informe WHERE desviacion ORDER BY alerta DESC, tabla, tenant NULLS FIRST;

\echo
\echo '=== 4. Auditoría y export WORM ==='
SELECT i.tabla, i.particiones, i.particiones_worm, i.ultimo_export_worm::timestamp(0) AS ultimo_export_worm,
       i.dato_mas_antiguo::timestamp(0) AS evento_mas_antiguo, i.ultima_purga::timestamp(0) AS ultimo_descarte,
       i.ultima_omision, i.accion
  FROM informe i WHERE i.tabla = 'audit.audit_events';
SELECT v.particion, v.desde::date, v.hasta::date, w.id AS comprobante, w.filas,
       w.verificado_en::timestamp(0) AS verificado_en, left(w.archivo_sha256, 16) AS archivo_sha256
  FROM audit.v_particiones_worm v
  LEFT JOIN audit.worm_exports w ON w.id = v.ultimo_export_id
 ORDER BY v.desde;

\echo
\echo '=== 5. Últimas ejecuciones de purga ==='
SELECT r.entidad, coalesce(r.tenant_id, '(plataforma)') AS ambito, r.accion, count(*) AS veces,
       max(r.ejecutado_en)::timestamp(0) AS ultima, sum(r.filas) AS filas
  FROM core.retention_purgas r
 GROUP BY r.entidad, r.tenant_id, r.accion ORDER BY r.entidad, ambito, r.accion;
SELECT l.tarea, count(*) AS ejecuciones, max(l.ejecutada_en)::timestamp(0) AS ultima, sum(l.filas) AS filas
  FROM core.purgas_log l GROUP BY l.tarea ORDER BY l.tarea;

\echo
\echo '=== 6. Tablas que se conservan (política conservar) ==='
SELECT tabla, particionada, justificacion
  FROM informe WHERE estado = 'conservar' ORDER BY tabla;

\echo
\echo '=== 7. core.validar_integridad() (V26 incluido) ==='
SELECT chequeo, problemas FROM core.validar_integridad()
 WHERE problemas > 0 OR chequeo = 'V26_tabla_sin_retencion' ORDER BY chequeo;

DROP TABLE informe;
