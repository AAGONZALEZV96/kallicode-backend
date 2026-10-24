-- Migración 037: Particionado LIST por tenant en colecciones vectoriales (KC-DB-08-4)
-- Convierte vec.definition_embeddings y vec.doc_chunks a tablas particionadas por LIST (tenant_id).

-- ============================================================================
-- 1. Particionado de vec.definition_embeddings
-- ============================================================================
ALTER TABLE vec.definition_embeddings RENAME TO definition_embeddings_old;
ALTER TABLE vec.definition_embeddings_old RENAME CONSTRAINT definition_embeddings_pkey TO definition_embeddings_old_pkey;
ALTER TABLE vec.definition_embeddings_old DROP CONSTRAINT IF EXISTS de_nodo_uq;
DROP INDEX IF EXISTS vec.definition_emb_tenant_ix;

CREATE TABLE vec.definition_embeddings (
    id text NOT NULL,
    tenant_id text NOT NULL,
    nodo_ref text NOT NULL,
    capa text NOT NULL,
    nombre text NOT NULL,
    descripcion text NOT NULL,
    modulo text,
    embedding vector(1024) NOT NULL,
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    modelo_embedding text NOT NULL DEFAULT 'bge-m3',
    texto_hash text NOT NULL,
    CONSTRAINT definition_embeddings_pkey PRIMARY KEY (tenant_id, id),
    CONSTRAINT de_nodo_uq UNIQUE (tenant_id, nodo_ref),
    CONSTRAINT de_capa_chk CHECK (capa IN ('definicion', 'negocio', 'codigo')),
    CONSTRAINT definition_embeddings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id) ON DELETE CASCADE
) PARTITION BY LIST (tenant_id);

CREATE TABLE vec.definition_embeddings_default PARTITION OF vec.definition_embeddings DEFAULT;

INSERT INTO vec.definition_embeddings (
    id, tenant_id, nodo_ref, capa, nombre, descripcion, modulo,
    embedding, actualizado_en, modelo_embedding, texto_hash
)
SELECT
    id, tenant_id, nodo_ref, capa, nombre, descripcion, modulo,
    embedding, actualizado_en, modelo_embedding, texto_hash
FROM vec.definition_embeddings_old;

DROP TABLE vec.definition_embeddings_old;

-- ============================================================================
-- 2. Particionado de vec.doc_chunks (Ingesta documental CodeMapping)
-- ============================================================================
DROP TABLE IF EXISTS vec.doc_chunks CASCADE;

CREATE TABLE vec.doc_chunks (
    id text NOT NULL,
    tenant_id text NOT NULL,
    document_id text NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    embedding vector(1024) NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT doc_chunks_pkey PRIMARY KEY (tenant_id, id),
    CONSTRAINT doc_chunks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id) ON DELETE CASCADE
) PARTITION BY LIST (tenant_id);

CREATE TABLE vec.doc_chunks_default PARTITION OF vec.doc_chunks DEFAULT;

-- ============================================================================
-- 3. Row-Level Security y aislamiento multi-tenant
-- ============================================================================
ALTER TABLE vec.definition_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE vec.definition_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON vec.definition_embeddings;
CREATE POLICY tenant_isolation ON vec.definition_embeddings FOR ALL USING (tenant_id = core.current_tenant());

ALTER TABLE vec.doc_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE vec.doc_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON vec.doc_chunks;
CREATE POLICY tenant_isolation ON vec.doc_chunks FOR ALL USING (tenant_id = core.current_tenant());