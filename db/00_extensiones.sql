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
