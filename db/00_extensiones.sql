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
