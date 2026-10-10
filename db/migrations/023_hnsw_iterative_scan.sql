-- kc:sin-transaccion
DROP INDEX CONCURRENTLY IF EXISTS vec.definition_embeddings_hnsw_ix;
CREATE INDEX CONCURRENTLY IF NOT EXISTS definition_embeddings_hnsw_ix ON vec.definition_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);