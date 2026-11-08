-- ============================================================================
-- KALLICODE · 039_ingesta_docs.sql
-- KC-CM-08-1 (CM-F22) — Paso 5/5 del pipeline: ingesta de documentación.
--
-- La 037 recreó vec.doc_chunks particionada por tenant con lo mínimo
-- (document_id, chunk_index, content, embedding) y en el camino perdió lo que
-- 01_schema.sql garantizaba: la unicidad documento + chunk (sin ella reprocesar
-- un documento duplica sus chunks) y la FK a core.onboarding_documents (sin ella
-- un chunk puede sobrevivir a su documento). Esta migración las restituye sobre
-- la tabla particionada y agrega lo que la ingesta necesita:
--
--   1. core.onboarding_documents: motivo_fallo + procesado_en + chunks_count.
--      Un documento 'fallido' SIEMPRE trae motivo (od_motivo_chk); uno que no
--      falló, nunca (al reprocesar con éxito se limpia).
--   2. vec.doc_chunks: seccion, tipo_documento, metadatos, modelo_embedding,
--      texto_hash (re-embeber solo si cambió, como el resto de vec.*),
--      UNIQUE (tenant_id, document_id, chunk_index) y FK compuesta al documento.
--      Las columnas de la 037 (document_id, chunk_index, content) se conservan:
--      renombrarlas sería un cambio destructivo para quien ya las lea.
--   3. cm.doc_chunk_links: enlace chunk -> nodo del grafo que describe
--      (léxico o semántico, con score), con FKs compuestas a vec.doc_chunks y
--      cm.nodes en cascada y RLS como el resto de cm.*.
--
-- No es destructiva: solo ADD COLUMN / ADD CONSTRAINT / CREATE TABLE. Si hay
-- chunks huérfanos o duplicados previos, la FK o la UNIQUE fallan y la
-- migración entera se revierte sin tocar nada (no se borran datos a ciegas).
-- PG16 no admite FKs NOT VALID en tablas particionadas: se validan al crearse.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. core.onboarding_documents: estado terminal con motivo
-- ---------------------------------------------------------------------------
ALTER TABLE core.onboarding_documents
    ADD COLUMN motivo_fallo text,
    ADD COLUMN procesado_en timestamptz,
    ADD COLUMN chunks_count integer;

ALTER TABLE core.onboarding_documents
    ADD CONSTRAINT od_motivo_chk
        CHECK ((estado = 'fallido') = (motivo_fallo IS NOT NULL AND motivo_fallo <> '')),
    ADD CONSTRAINT od_chunks_chk
        CHECK (chunks_count IS NULL OR chunks_count >= 0);

-- La ingesta recorre los pendientes del tenant.
CREATE INDEX onboarding_documents_pendientes_ix
    ON core.onboarding_documents (tenant_id, creado_en)
    WHERE estado = 'pendiente';

-- ---------------------------------------------------------------------------
-- 2. vec.doc_chunks: metadatos, hash, unicidad y FK al documento
-- ---------------------------------------------------------------------------
ALTER TABLE vec.doc_chunks
    ADD COLUMN seccion          text NOT NULL DEFAULT '',
    ADD COLUMN tipo_documento   text NOT NULL DEFAULT 'otro',
    ADD COLUMN metadatos        jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN modelo_embedding text NOT NULL DEFAULT 'bge-m3',
    ADD COLUMN texto_hash       text NOT NULL DEFAULT '',
    ADD COLUMN actualizado_en   timestamptz NOT NULL DEFAULT now();

-- texto_hash no tiene un valor por defecto con sentido: solo se dio para poder
-- agregar la columna NOT NULL; las filas nuevas deben traerlo.
ALTER TABLE vec.doc_chunks ALTER COLUMN texto_hash DROP DEFAULT;

ALTER TABLE vec.doc_chunks
    ADD CONSTRAINT dc_tipo_chk
        CHECK (tipo_documento IN ('manual', 'spec', 'adr', 'negocio', 'otro')),
    ADD CONSTRAINT dc_chunk_index_chk CHECK (chunk_index >= 0),
    -- Incluye tenant_id: en una tabla particionada toda UNIQUE debe contener la
    -- clave de partición. document_id ya es único por tenant, así que equivale
    -- a la dc_chunk_uq (documento_id, chunk_idx) original de 01_schema.sql.
    ADD CONSTRAINT dc_chunk_uq UNIQUE (tenant_id, document_id, chunk_index),
    ADD CONSTRAINT doc_chunks_document_id_fkey
        FOREIGN KEY (tenant_id, document_id)
        REFERENCES core.onboarding_documents (tenant_id, id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- 3. cm.doc_chunk_links: chunk -> nodo del grafo
-- ---------------------------------------------------------------------------
CREATE TABLE cm.doc_chunk_links (
    tenant_id  text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    chunk_id   text NOT NULL,
    nodo_ref   text NOT NULL,
    metodo     text NOT NULL,
    score      real NOT NULL,
    creado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chunk_id, nodo_ref),
    CONSTRAINT dcl_metodo_chk CHECK (metodo IN ('lexico', 'semantico')),
    CONSTRAINT dcl_score_chk  CHECK (score >= 0 AND score <= 1),
    CONSTRAINT dcl_chunk_fk FOREIGN KEY (tenant_id, chunk_id)
        REFERENCES vec.doc_chunks (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT dcl_nodo_fk FOREIGN KEY (tenant_id, nodo_ref)
        REFERENCES cm.nodes (tenant_id, id) ON DELETE CASCADE
);

-- "¿Qué documentación describe este nodo?" (impact-preview, Diseño).
CREATE INDEX doc_chunk_links_nodo_ix ON cm.doc_chunk_links (tenant_id, nodo_ref);

ALTER TABLE cm.doc_chunk_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.doc_chunk_links FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.doc_chunk_links
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE ON cm.doc_chunk_links TO kallicode_app, kallicode_worker;
