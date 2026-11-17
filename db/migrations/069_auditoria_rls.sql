-- ============================================================================
-- KALLICODE · 069_auditoria_rls.sql
-- KC-DB-12-3 (5 SP) — Auditoría final de RLS, privilegios y roles.   CLASE B (toca RLS y privilegios).
--
-- La auditoría sobre el esquema real (66 migraciones) encontró lo siguiente; esta migración lo corrige y deja las
-- herramientas para que no vuelva a pasar:
--
--   1. FUGA ENTRE TENANTS por una partición hija. vec.definition_embeddings_default (partición de una tabla
--      particionada) tenía SELECT/INSERT/UPDATE/DELETE para kallicode_app y kallicode_worker. Una partición no hereda
--      las políticas RLS del padre: quien tiene permiso sobre ella la lee ENTERA, de todos los tenants. Lo concedió la
--      migración 037 (GRANT ... ON ALL TABLES IN SCHEMA vec) DESPUÉS de crear la partición, y el event trigger que quita
--      ese acceso (kc_particiones_sin_acceso, db/00_extensiones.sql) solo actúa en el momento del DDL. Aquí se revoca el
--      acceso a TODAS las particiones de core, audit, vec y cm; la aplicación llega a ellas solo a través del padre.
--   2. core.retention_purgas tiene tenant_id y no tenía RLS. Se activa RLS + FORCE con política por tenant (la
--      aplicación no tiene privilegios sobre ella: es defensa en profundidad).
--   3. 66 de las 71 políticas de nuestros esquemas no tenían cláusula TO (valían para PUBLIC): tenant_isolation en 65
--      tablas y plan_prices_solo_lectura. Se acotan a kallicode_app y kallicode_worker (kallicode_dev hereda de app).
--      La cláusula TO es defensa en profundidad: ningún rol nuevo queda cubierto por una política sin que alguien lo decida.
--   4. Escrituras de kallicode_app sobre catálogos globales sin RLS (core.user_roles, core.pipeline_stages) y de
--      kallicode_app y kallicode_worker sobre vec.acta_conversion_capa: venían del DEFAULT PRIVILEGES, nada del código las
--      usa y permitían a un tenant alterar un catálogo que comparten todos. Se revocan.
--
-- Y las herramientas de la auditoría:
--   * core.excepciones_aislamiento: toda tabla sin RLS y toda política que no filtra por tenant tiene aquí su
--     justificación escrita. Sin ella, el informe la marca como INCUMPLE.
--   * core.informe_aislamiento() y core.informe_politicas_rls(): el informe, generado del catálogo. docs/auditoria_rls.md
--     es su volcado y una prueba lo compara.
--
-- Cambios de privilegios solo REVOCAN (nada se concede): el código que funcionaba sigue funcionando; si alguna prueba de
-- API o de workers fallara, sería porque dependía de un acceso que no debía tener. Re-ejecutable. Sin BEGIN/COMMIT.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ----------------------------------------------------------------------------
-- 1. Particiones hijas: sin acceso directo para la aplicación ni para el worker.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.oid::regclass AS rel
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relispartition AND c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'audit', 'vec', 'cm')
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC, kallicode_app, kallicode_worker', r.rel);
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Escrituras sin justificar sobre catálogos globales.
-- ----------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON core.user_roles, core.pipeline_stages FROM kallicode_app, kallicode_worker;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON vec.acta_conversion_capa FROM kallicode_app, kallicode_worker;

-- ----------------------------------------------------------------------------
-- 3. core.retention_purgas: tiene tenant_id, así que tiene RLS.
-- ----------------------------------------------------------------------------
ALTER TABLE core.retention_purgas ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.retention_purgas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON core.retention_purgas;
CREATE POLICY tenant_isolation ON core.retention_purgas
    TO kallicode_app, kallicode_worker
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

-- ----------------------------------------------------------------------------
-- 4. Toda política de nuestros esquemas con cláusula TO.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT p.polname, c.oid::regclass AS rel
          FROM pg_policy p
          JOIN pg_class c ON c.oid = p.polrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE p.polroles = '{0}'::oid[] AND n.nspname IN ('core', 'audit', 'vec', 'cm')
    LOOP
        EXECUTE format('ALTER POLICY %I ON %s TO kallicode_app, kallicode_worker', r.polname, r.rel);
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- 5. Excepciones justificadas.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.excepciones_aislamiento (
    entidad            text PRIMARY KEY,
    tipo               text    NOT NULL,
    motivo             text    NOT NULL,
    permite_escritura  boolean NOT NULL DEFAULT false,
    ticket             text    NOT NULL DEFAULT 'KC-DB-12-3',
    CONSTRAINT excepciones_aislamiento_tipo_chk CHECK (tipo IN ('tabla_sin_rls', 'politica_sin_filtro_de_tenant')),
    CONSTRAINT excepciones_aislamiento_motivo_chk CHECK (char_length(btrim(motivo)) >= 20)
);
COMMENT ON TABLE core.excepciones_aislamiento IS
'KC-DB-12-3: justificación escrita de cada tabla sin RLS y de cada política que no filtra por tenant. core.informe_aislamiento() marca como INCUMPLE todo lo que no figure aquí.';
COMMENT ON COLUMN core.excepciones_aislamiento.entidad IS 'esquema.tabla para una tabla sin RLS; esquema.tabla#política para una política que no filtra por tenant.';
COMMENT ON COLUMN core.excepciones_aislamiento.tipo IS 'tabla_sin_rls o politica_sin_filtro_de_tenant.';
COMMENT ON COLUMN core.excepciones_aislamiento.motivo IS 'Por qué es seguro que no filtre por tenant (mínimo 20 caracteres): qué contiene y quién puede acceder.';
COMMENT ON COLUMN core.excepciones_aislamiento.permite_escritura IS 'true si se justifica que kallicode_app o kallicode_worker escriban en la tabla sin RLS; por defecto no.';
COMMENT ON COLUMN core.excepciones_aislamiento.ticket IS 'Ticket que decidió la excepción.';
COMMENT ON CONSTRAINT excepciones_aislamiento_pkey ON core.excepciones_aislamiento IS 'Una excepción por entidad.';
COMMENT ON CONSTRAINT excepciones_aislamiento_tipo_chk ON core.excepciones_aislamiento IS 'Solo los dos tipos de excepción que reconoce el informe.';
COMMENT ON CONSTRAINT excepciones_aislamiento_motivo_chk ON core.excepciones_aislamiento IS 'Una excepción sin una explicación real no es una justificación.';
REVOKE ALL ON core.excepciones_aislamiento FROM PUBLIC, kallicode_app, kallicode_worker;

INSERT INTO core.excepciones_aislamiento (entidad, tipo, motivo, permite_escritura) VALUES
  ('audit.tenant_deletion_receipts', 'tabla_sin_rls', 'Actas de eliminación de un tenant, con el tenant ya pseudonimizado (hash): sobreviven al borrado del tenant y no llevan su identificador. Solo kallicode_admin accede.', false),
  ('audit.worm_exports', 'tabla_sin_rls', 'Comprobantes de export WORM de la auditoría (append-only). Cada fila resume varios tenants de una partición, así que no pertenece a uno solo. Solo kallicode_admin accede.', false),
  ('cm.edge_types', 'tabla_sin_rls', 'Catálogo global de tipos de arista del grafo, sembrado por migraciones. Lectura para la aplicación y el worker; sin datos de ningún tenant.', false),
  ('cm.node_types', 'tabla_sin_rls', 'Catálogo global de tipos de nodo del grafo, sembrado por migraciones. Lectura para la aplicación y el worker; sin datos de ningún tenant.', false),
  ('core.excepciones_aislamiento', 'tabla_sin_rls', 'Esta misma tabla de justificaciones: es de gobierno de la plataforma, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('core.objetivos_recuperacion', 'tabla_sin_rls', 'Objetivos RPO/RTO de la plataforma y su aprobación (KC-DB-11-1): gobierno, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('core.pii_ventanas', 'tabla_sin_rls', 'Ventanas de minimización de PII por regla (KC-DB-10-3): configuración de la plataforma, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('core.pipeline_stages', 'tabla_sin_rls', 'Catálogo global de etapas del pipeline (migración 049). Lectura para la aplicación y el worker; las escrituras se revocaron en KC-DB-12-3.', false),
  ('core.plans', 'tabla_sin_rls', 'Catálogo global de planes comerciales. Solo lectura para la aplicación; sin datos de tenants.', false),
  ('core.politica_recuperacion', 'tabla_sin_rls', 'Política de recuperación por tabla (KC-DB-11-4): gobierno de la plataforma, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('core.purgas_log', 'tabla_sin_rls', 'Registro de ejecuciones de las purgas por TTL y de la minimización de PII (KC-DB-10-3): solo tarea y número de filas, sin identificadores de tenant. Solo kallicode_admin accede.', false),
  ('core.retencion_dedicada', 'tabla_sin_rls', 'Tablas purgadas fuera del motor de retención con su plazo (KC-DB-10-4): configuración de la plataforma, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('core.schema_migrations', 'tabla_sin_rls', 'Ledger de migraciones del esquema (sha256, fecha, etiqueta de versión): solo kallicode_admin lo escribe; la aplicación y el worker solo pueden leerlo vía core.version_esquema().', false),
  ('core.user_roles', 'tabla_sin_rls', 'Catálogo global de roles de usuario (migración 049). Lectura para la aplicación y el worker; las escrituras se revocaron en KC-DB-12-3.', false),
  ('vec.acta_conversion_capa', 'tabla_sin_rls', 'Acta histórica de la conversión de capas de embeddings (KC-DB-04-4): datos de migración, sin identificadores de tenant. Lectura; las escrituras se revocaron en KC-DB-12-3.', false),
  ('vec.hnsw_catalogo', 'tabla_sin_rls', 'Definición de los índices HNSW (KC-DB-08-2): configuración de operación, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('vec.hnsw_metricas', 'tabla_sin_rls', 'Mediciones de tamaño y duración de los índices HNSW: operación, sin datos de tenants. Solo kallicode_admin accede.', false),
  ('vec.parametros_busqueda', 'tabla_sin_rls', 'Parámetros de la búsqueda vectorial por caso de uso (ef_search, etc.): configuración de la plataforma. Lectura para la aplicación y el worker.', false),
  ('core.plan_prices#plan_prices_solo_lectura', 'politica_sin_filtro_de_tenant', 'Lista de precios de la plataforma: el mismo dato para todos los tenants, solo lectura (FOR SELECT) y sin columna de tenant.', false),
  ('core.webhook_inbox#worker_colas', 'politica_sin_filtro_de_tenant', 'El worker barre la cola de webhooks de TODOS los tenants (migración 010): solo kallicode_worker, por política y no por BYPASSRLS; las operaciones las limita el GRANT (SELECT, UPDATE, DELETE).', false),
  ('core.jobs#worker_colas', 'politica_sin_filtro_de_tenant', 'El planificador del worker lee los jobs pendientes de todos los tenants (migración 010): solo kallicode_worker y solo SELECT.', false),
  ('core.refresh_tokens#worker_colas', 'politica_sin_filtro_de_tenant', 'La purga de tokens vencidos del worker barre todos los tenants (migración 010): solo kallicode_worker y solo SELECT y DELETE.', false),
  ('core.ticket_attachments#worker_colas', 'politica_sin_filtro_de_tenant', 'La purga de adjuntos huérfanos del worker barre todos los tenants (migración 010): solo kallicode_worker y solo SELECT y DELETE.', false),
  ('core.idempotency_keys#worker_colas', 'politica_sin_filtro_de_tenant', 'La purga de claves de idempotencia y el worker de ingesta operan sobre todos los tenants (migraciones 010 y 054): solo kallicode_worker.', false)
ON CONFLICT (entidad) DO NOTHING;

INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo) VALUES
    (NULL, 'core.excepciones_aislamiento', NULL, 'conservar',
     'Justificaciones del aislamiento (KC-DB-12-3): pocas filas, las cambia una migración, sin plazo propio.')
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;

-- ----------------------------------------------------------------------------
-- 6. El informe, generado del catálogo (una fila por tabla y una por política).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.informe_aislamiento()
RETURNS TABLE (
    tabla                    text,
    tiene_tenant_id          boolean,
    rls                      boolean,
    forzada                  boolean,
    politicas                integer,
    politicas_sin_to         integer,
    priv_app                 text,
    priv_worker              text,
    particiones              integer,
    particiones_con_acceso   integer,
    probatoria               boolean,
    estado                   text,
    justificacion            text
)
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    -- Tablas probatorias (KC-DB-05-2): ni la aplicación ni el worker pueden modificarlas ni borrarlas.
    v_probatorias constant text[] := ARRAY[
        'audit.audit_events', 'core.gate_signatures', 'core.llm_steps', 'core.job_transitions',
        'core.qa_runs', 'core.qa_results', 'core.usage_loc', 'core.security_findings'];
    r        record;
    v_priv   text;
    v_prob   text;
    v_exc    record;
BEGIN
    FOR r IN
        SELECT c.oid, n.nspname || '.' || c.relname AS nombre, c.relrowsecurity AS rls, c.relforcerowsecurity AS forzada,
               EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS tiene_tenant
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname IN ('core', 'audit', 'vec', 'cm')
           AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
         ORDER BY 2
    LOOP
        tabla := r.nombre; tiene_tenant_id := r.tiene_tenant; rls := r.rls; forzada := r.forzada;
        SELECT count(*)::integer, count(*) FILTER (WHERE p.polroles = '{0}'::oid[])::integer
          INTO politicas, politicas_sin_to FROM pg_policy p WHERE p.polrelid = r.oid;
        SELECT coalesce(string_agg(x, ','), '-') INTO priv_app
          FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) x WHERE has_table_privilege('kallicode_app', r.oid, x);
        SELECT coalesce(string_agg(x, ','), '-') INTO priv_worker
          FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) x WHERE has_table_privilege('kallicode_worker', r.oid, x);
        SELECT count(*)::integer,
               count(*) FILTER (WHERE has_table_privilege('kallicode_app', i.inhrelid, 'SELECT,INSERT,UPDATE,DELETE')
                                  OR has_table_privilege('kallicode_worker', i.inhrelid, 'SELECT,INSERT,UPDATE,DELETE')
                                  OR has_table_privilege('kallicode_app', i.inhrelid, 'SELECT')
                                  OR has_table_privilege('kallicode_worker', i.inhrelid, 'SELECT'))::integer
          INTO particiones, particiones_con_acceso
          FROM pg_inherits i WHERE i.inhparent = r.oid;
        probatoria := r.nombre = ANY (v_probatorias);
        SELECT e.* INTO v_exc FROM core.excepciones_aislamiento e WHERE e.entidad = r.nombre AND e.tipo = 'tabla_sin_rls';
        justificacion := v_exc.motivo;

        v_prob := '';
        IF particiones_con_acceso > 0 THEN
            v_prob := v_prob || '; una partición hija es accesible para la aplicación o el worker (leería todos los tenants)';
        END IF;
        IF probatoria AND (priv_app ~ '(UPDATE|DELETE|TRUNCATE)' OR priv_worker ~ '(UPDATE|DELETE|TRUNCATE)') THEN
            v_prob := v_prob || '; tabla probatoria con escritura destructiva';
        END IF;
        IF r.rls THEN
            IF NOT r.forzada THEN v_prob := v_prob || '; RLS sin FORCE'; END IF;
            IF politicas = 0 THEN v_prob := v_prob || '; RLS sin ninguna política'; END IF;
            IF politicas_sin_to > 0 THEN v_prob := v_prob || '; ' || politicas_sin_to || ' política(s) sin cláusula TO'; END IF;
        ELSE
            IF r.tiene_tenant THEN v_prob := v_prob || '; tiene tenant_id y no tiene RLS'; END IF;
            IF v_exc.entidad IS NULL THEN
                v_prob := v_prob || '; sin RLS y sin excepción justificada';
            ELSIF (priv_app ~ '(INSERT|UPDATE|DELETE|TRUNCATE)' OR priv_worker ~ '(INSERT|UPDATE|DELETE|TRUNCATE)') AND NOT v_exc.permite_escritura THEN
                v_prob := v_prob || '; escritura sobre una tabla sin RLS sin justificar';
            END IF;
        END IF;
        estado := CASE WHEN v_prob <> '' THEN 'INCUMPLE' || v_prob
                       WHEN r.rls THEN 'aislada'
                       ELSE 'excepcion_justificada' END;
        RETURN NEXT;
    END LOOP;
END
$f$;
COMMENT ON FUNCTION core.informe_aislamiento() IS
'KC-DB-12-3: informe de aislamiento por tabla (RLS, FORCE, políticas, cláusula TO, privilegios de app y worker, acceso a particiones hijas, tablas probatorias) con su estado: aislada, excepcion_justificada o INCUMPLE. Sale del catálogo.';
REVOKE ALL ON FUNCTION core.informe_aislamiento() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.informe_aislamiento() TO kallicode_admin;

CREATE OR REPLACE FUNCTION core.informe_politicas_rls()
RETURNS TABLE (
    tabla            text,
    politica         text,
    comando          text,
    roles            text,
    filtra_por_tenant boolean,
    estado           text,
    justificacion    text
)
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT n.nspname || '.' || c.relname,
           p.polname::text,
           CASE p.polcmd WHEN '*' THEN 'ALL' WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT' WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' END,
           CASE WHEN p.polroles = '{0}'::oid[] THEN 'PUBLIC'
                ELSE (SELECT string_agg(r.rolname::text, ', ' ORDER BY r.rolname) FROM pg_roles r WHERE r.oid = ANY (p.polroles)) END,
           coalesce(pg_get_expr(p.polqual, p.polrelid), '') LIKE '%core.current_tenant()%',
           CASE WHEN p.polroles = '{0}'::oid[] THEN 'INCUMPLE: sin cláusula TO'
                WHEN coalesce(pg_get_expr(p.polqual, p.polrelid), '') LIKE '%core.current_tenant()%' THEN 'filtra por tenant'
                WHEN e.entidad IS NOT NULL THEN 'excepcion_justificada'
                ELSE 'INCUMPLE: no filtra por tenant y no está justificada' END,
           e.motivo
      FROM pg_policy p
      JOIN pg_class c ON c.oid = p.polrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN core.excepciones_aislamiento e
             ON e.entidad = n.nspname || '.' || c.relname || '#' || p.polname AND e.tipo = 'politica_sin_filtro_de_tenant'
     WHERE n.nspname IN ('core', 'audit', 'vec', 'cm')
     ORDER BY 1, 2
$f$;
COMMENT ON FUNCTION core.informe_politicas_rls() IS
'KC-DB-12-3: una fila por política RLS de core, audit, vec y cm: comando, roles (cláusula TO), si filtra por tenant y su estado (filtra por tenant, excepcion_justificada o INCUMPLE). Sale del catálogo.';
REVOKE ALL ON FUNCTION core.informe_politicas_rls() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.informe_politicas_rls() TO kallicode_admin;
