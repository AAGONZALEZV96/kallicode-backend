-- ============================================================================
-- KALLICODE · db/tools/ensayo_particion_auditoria.sql
-- KC-DB-07-1 — Carga sintética para ensayar 033_particion_auditoria.sql (y su
-- reversión) con un volumen realista. SOLO PARA ENSAYOS: nunca en producción.
--
-- Prepara una audit.audit_events SIMPLE (la migración parte de ahí) con:
--   * :filas eventos sintéticos (por defecto 5.000.000) repartidos en 30 meses
--     entre tres tenants, escritos con el trigger de sellado desactivado. Sus
--     sellos son hashes válidos en forma (64 hex) pero NO forman una cadena.
--   * 2.000 eventos de un tenant aparte (org_ensayo_cadena) insertados a través
--     del trigger de sellado, que SÍ forman una cadena íntegra: sirven para
--     comprobar que la conversión conserva los sellos (audit.verify_chain).
--
-- Procedimiento completo (base recién migrada hasta la 032, o revertida con
-- db/tools/revertir_particion_auditoria.sql):
--     psql -U postgres -d <base> -v filas=5000000 -f db/tools/ensayo_particion_auditoria.sql
--     \timing on
--     \i db/migrations/033_particion_auditoria.sql          -- duración de la conversión
--     SELECT * FROM audit.verify_chain('org_ensayo_cadena'); -- integra = t
--     \i db/tools/revertir_particion_auditoria.sql          -- duración de la reversión
-- Resultados medidos: docs/particionado_auditoria.md.
-- ============================================================================

\if :{?filas}
\else
\set filas 5000000
\endif

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF (SELECT relkind FROM pg_class WHERE oid = 'audit.audit_events'::regclass) <> 'r' THEN
        RAISE EXCEPTION 'audit.audit_events ya está particionada: revierta primero con db/tools/revertir_particion_auditoria.sql';
    END IF;
END $$;

INSERT INTO core.organizations (id, nombre)
SELECT 'org_ensayo_' || t, 'Tenant de ensayo ' || t
  FROM unnest(ARRAY['1', '2', '3', 'cadena']) AS t
ON CONFLICT (id) DO NOTHING;

-- 1. Volumen sintético, sin pasar por el trigger de sellado.
ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal;
INSERT INTO audit.audit_events
       (tenant_id, ticket_id, actor_tipo, evento, resumen, datos, sello_previo, sello, creado_en, version_sello)
SELECT 'org_ensayo_' || (1 + g % 3),
       'tk_' || (g % 50000),
       (ARRAY['humano', 'agente', 'sistema'])[1 + g % 3],
       (ARRAY['ticket_creado', 'gate_firmado', 'spec_generada', 'etapa_avanzada'])[1 + g % 4],
       'Evento sintético ' || g,
       jsonb_build_object('n', g),
       repeat('0', 64),
       encode(sha256(convert_to(g::text, 'UTF8')), 'hex'),
       now() - (random() * interval '900 days'),
       2
  FROM generate_series(1, :filas) AS g;
ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal;

-- 2. Cadena íntegra de 2.000 eventos, a través del trigger de sellado.
SELECT set_config('app.tenant_id', 'org_ensayo_cadena', false);
INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen)
SELECT 'org_ensayo_cadena', 'sistema', 'evento_ensayo', 'Evento de la cadena de ensayo ' || g
  FROM generate_series(1, 2000) AS g;
SELECT set_config('app.tenant_id', '', false);

ANALYZE audit.audit_events;

SELECT count(*)                                  AS filas,
       pg_size_pretty(pg_total_relation_size('audit.audit_events')) AS tamano_total,
       min(creado_en)::date                      AS desde,
       max(creado_en)::date                      AS hasta
  FROM audit.audit_events;
SELECT * FROM audit.verify_chain('org_ensayo_cadena');
