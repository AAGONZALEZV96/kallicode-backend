-- ============================================================================
-- KALLICODE · 00_extensiones.sql — Extensiones de PostgreSQL (KC-DB-01-5)
-- Junto con 00_roles.sql, es lo ÚNICO que requiere superusuario. Todo lo
-- demás (01..05.sql y db/migrations/) lo aplica kallicode_admin.
--
-- Nota Azure: habilitar las extensiones en el servidor antes de ejecutar:
--   az postgres flexible-server parameter set --name azure.extensions \
--      --value vector,pg_trgm ...
-- Los hashes usan la función nativa sha256() (PostgreSQL >= 11): sin extensión.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;   -- búsqueda de texto en títulos
CREATE EXTENSION IF NOT EXISTS vector  WITH SCHEMA public;   -- pgvector: embeddings

-- pg_stat_statements (KC-DB-06-1): mide qué consultas cuestan más y qué índices se usan; la lee
-- db/tools/informe_indices.sql. Para que recoja datos Postgres debe arrancar con
-- shared_preload_libraries = pg_stat_statements (ver docker-compose.yml); sin eso la extensión
-- existe pero no registra nada. La creación es opcional: si el servidor no la ofrece (en Azure
-- debe estar permitida en azure.extensions) el arranque NO falla y el informe omite esas secciones.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_stat_statements no disponible (%): el informe de indices omitira las consultas costosas', SQLERRM;
END $$;

-- pg_partman (KC-DB-07-1): crea y mantiene las particiones mensuales de audit.audit_events
-- (db/migrations/033_particion_auditoria.sql). Requiere una imagen/servidor que la incluya
-- (db/postgres/Dockerfile; en Azure, permitirla en azure.extensions y, para el mantenimiento
-- automático, pg_partman_bgw en shared_preload_libraries). Si no está disponible el arranque
-- NO falla, pero la migración 033 se negará a aplicarse con un mensaje explícito.
-- kallicode_admin (dueño del esquema, no superusuario) la opera sin privilegios especiales.
DO $$
BEGIN
    CREATE SCHEMA IF NOT EXISTS partman;
    CREATE EXTENSION IF NOT EXISTS pg_partman WITH SCHEMA partman;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_partman no disponible (%): la migracion 033 (particionado de audit_events) no podra aplicarse', SQLERRM;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman')
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_admin') THEN
        GRANT USAGE, CREATE ON SCHEMA partman TO kallicode_admin;
        GRANT ALL ON ALL TABLES IN SCHEMA partman TO kallicode_admin;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA partman TO kallicode_admin;
        GRANT EXECUTE ON ALL PROCEDURES IN SCHEMA partman TO kallicode_admin;
    END IF;
END $$;

-- Particiones sin acceso para la aplicación (KC-DB-07-2, reforzado en KC-DB-07-3).
-- 04_rls.sql concede por defecto SELECT/INSERT/UPDATE/DELETE a kallicode_app sobre TODA tabla nueva
-- de core y vec, y una partición es una tabla nueva. Pero la política RLS vive en la tabla padre:
-- una partición no la hereda, y quien tenga permiso sobre ella la leería ENTERA, de todos los
-- tenants. La aplicación solo debe llegar a las particiones a través del padre. Este event trigger
-- (necesita superusuario, por eso está aquí) quita todo acceso a kallicode_app y kallicode_worker
-- a las particiones de core y vec, también las que cree pg_partman_bgw cada mes.
-- pg_partman crea las particiones de dos maneras: CREATE TABLE ... PARTITION OF (la tabla ya es
-- partición al terminar el comando) o CREATE TABLE suelta + ALTER TABLE padre ATTACH PARTITION
-- (en el CREATE todavía no lo es, y el comando del ATTACH reporta al PADRE). Por eso el trigger
-- mira el objeto del comando y, si es una tabla particionada, sus particiones.
CREATE OR REPLACE FUNCTION public.kc_revocar_acceso_particiones() RETURNS event_trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT DISTINCT c.oid::regclass::text AS tabla
          FROM pg_event_trigger_ddl_commands() d
          JOIN pg_class k ON k.oid = d.objid
          JOIN pg_class c ON c.oid = k.oid
                          OR c.oid IN (SELECT i.inhrelid FROM pg_inherits i WHERE i.inhparent = k.oid)
         WHERE d.object_type = 'table' AND d.schema_name IN ('core', 'vec') AND c.relispartition
           AND (has_table_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE, DELETE')
                OR has_table_privilege('kallicode_worker', c.oid, 'SELECT, INSERT, UPDATE, DELETE'))
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM kallicode_app, kallicode_worker', r.tabla);
    END LOOP;
END $$;

DROP EVENT TRIGGER IF EXISTS kc_particiones_sin_acceso;
CREATE EVENT TRIGGER kc_particiones_sin_acceso ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE', 'ALTER TABLE')
    EXECUTE FUNCTION public.kc_revocar_acceso_particiones();

