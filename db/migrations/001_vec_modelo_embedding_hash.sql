-- ============================================================================
-- KALLICODE · 001_vec_modelo_embedding_hash.sql
-- KC-ONB-02-1 (DB-F18) — Migración DDL en vec.*: estandariza modelo de
-- embedding y hash de contenido en las tres tablas vectoriales.
--
-- vec.ticket_embeddings ya tenía modelo_embedding + texto_hash (01_schema.sql).
-- Esta migración agrega las mismas columnas a vec.doc_chunks y
-- vec.definition_embeddings, con el mismo propósito: permitir a los workers
-- saltarse el re-embeber cuando el contenido no cambió, y saber con qué
-- modelo se generó cada vector si en el futuro se migra a otro.
--
-- Convención de hash (ver apps/workers/.../normalizer.py):
--   sha256(texto_embebido).hexdigest()[:32]
--
-- Ambas tablas están vacías en todos los entornos existentes (no hay INSERT
-- en db/seeds/dev.sql), por lo que agregar texto_hash NOT NULL sin DEFAULT
-- es seguro: no hay filas que violen el constraint.
--
-- Índices HNSW: ya existen para las tres tablas desde 02_indexes.sql
-- (ticket_embeddings_hnsw_ix, doc_chunks_hnsw_ix, definition_embeddings_hnsw_ix,
-- todos m=16/ef_construction=64/coseno) — no requieren cambios en este ticket.
-- ============================================================================

BEGIN;

ALTER TABLE vec.doc_chunks
    ADD COLUMN modelo_embedding text NOT NULL DEFAULT 'bge-m3',
    ADD COLUMN texto_hash       text NOT NULL;              -- re-embeber solo si cambió

ALTER TABLE vec.definition_embeddings
    ADD COLUMN modelo_embedding text NOT NULL DEFAULT 'bge-m3',
    ADD COLUMN texto_hash       text NOT NULL;              -- re-embeber solo si cambió

COMMIT;
