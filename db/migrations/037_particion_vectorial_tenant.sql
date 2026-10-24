-- Migración 037: Particionado LIST por tenant en colecciones vectoriales (KC-DB-08-4)

-- ============================================================================
-- 1. Particionado de vec.definition_embeddings
-- ============================================================================
ALTER TABLE vec.definition_embeddings RENAME TO definition_embeddings_old;
ALTER TABLE vec.definition_embeddings_old RENAME CONSTRAINT definition_embeddings_pkey TO definition_embeddings_old_pkey;
ALTER TABLE vec.definition_embeddings_old DROP CONSTRAINT IF EXISTS de_nodo_uq;
DROP INDEX IF EXISTS vec.definition_emb_tenant_ix;
DROP INDEX IF EXISTS vec.definition_embeddings_hnsw_ix;

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
    CONSTRAINT de_capa_chk CHECK (capa = ANY (ARRAY['file'::text, 'symbol'::text, 'db_table'::text, 'db_column'::text, 'definition'::text, 'concept'::text])),
    CONSTRAINT definition_embeddings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id) ON DELETE CASCADE,
    CONSTRAINT de_nodo_fk FOREIGN KEY (tenant_id, nodo_ref) REFERENCES cm.nodes(tenant_id, id) ON DELETE CASCADE
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

CREATE INDEX definition_emb_tenant_ix ON vec.definition_embeddings (tenant_id, capa, modulo);
CREATE INDEX definition_embeddings_hnsw_ix ON vec.definition_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ============================================================================
-- 2. Particionado de vec.doc_chunks (Ingesta documental CodeMapping)
-- ============================================================================
DROP TABLE IF EXISTS vec.doc_chunks CASCADE;
DROP INDEX IF EXISTS vec.doc_chunks_tenant_ix;
DROP INDEX IF EXISTS vec.doc_chunks_hnsw_ix;

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

CREATE INDEX doc_chunks_tenant_ix ON vec.doc_chunks (tenant_id, document_id);
CREATE INDEX doc_chunks_hnsw_ix ON vec.doc_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ============================================================================
-- 3. Row-Level Security y Permisos
-- ============================================================================
ALTER TABLE vec.definition_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE vec.definition_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON vec.definition_embeddings;
CREATE POLICY tenant_isolation ON vec.definition_embeddings FOR ALL USING (tenant_id = core.current_tenant());

ALTER TABLE vec.doc_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE vec.doc_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON vec.doc_chunks;
CREATE POLICY tenant_isolation ON vec.doc_chunks FOR ALL USING (tenant_id = core.current_tenant());

ALTER TABLE vec.definition_embeddings OWNER TO kallicode_admin;
ALTER TABLE vec.definition_embeddings_default OWNER TO kallicode_admin;
ALTER TABLE vec.doc_chunks OWNER TO kallicode_admin;
ALTER TABLE vec.doc_chunks_default OWNER TO kallicode_admin;

GRANT ALL ON SCHEMA vec TO kallicode_admin;
GRANT ALL ON ALL TABLES IN SCHEMA vec TO kallicode_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA vec TO kallicode_app, kallicode_worker;
GRANT USAGE ON SCHEMA vec TO kallicode_app, kallicode_worker;