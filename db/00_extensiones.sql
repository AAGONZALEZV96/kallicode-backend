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
