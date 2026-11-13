-- ============================================================================
-- KALLICODE · 045_hnsw_opclase.sql
-- KC-DB-08-3 (DB-F31, BAJO) — Representación vectorial: el catálogo de índices HNSW guarda la clase de
-- operadores.
--
-- La evaluación de halfvec y de la cuantización binaria (docs/adr/ADR-022-representacion-vectorial.md,
-- packages/core/tests/bench_halfvec.py) concluyó MANTENER vector(1024) (float4): con el HNSW halfvec pierde
-- 4-5 puntos de recall@10 (el criterio de adopción es < 1) aunque el redondeo a float2 solo cuesta ~0,1.
-- Esta migración deja lista la otra mitad de la decisión: que cambiar de representación sea una operación
-- acotada y reversible, sin tocar el procedimiento de db/06_indices_concurrentes.sql, que antes tenía
-- escrito vector_cosine_ops y ahora lee la clase de operadores de vec.hnsw_catalogo.opclase.
--
-- El cambio de tipo (vector -> halfvec) NO se aplica aquí: está en db/propuestas/halfvec/ (aplicar_halfvec.sql
-- y revertir_halfvec.sql), ensayado en packages/core/tests/integracion/test_halfvec.py, para ejecutarse solo
-- si se cumple el criterio de reversión del ADR-022.
--
-- Nota: el ticket nombra db/migrations/030_halfvec.sql; la 030 está ocupada, esta es la 045.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

ALTER TABLE vec.hnsw_catalogo
    ADD COLUMN IF NOT EXISTS opclase text NOT NULL DEFAULT 'vector_cosine_ops';
ALTER TABLE vec.hnsw_catalogo DROP CONSTRAINT IF EXISTS hc_opclase_chk;
ALTER TABLE vec.hnsw_catalogo
    ADD CONSTRAINT hc_opclase_chk CHECK (opclase IN ('vector_cosine_ops', 'halfvec_cosine_ops'));
COMMENT ON COLUMN vec.hnsw_catalogo.opclase IS
'KC-DB-08-3: clase de operadores del HNSW; debe corresponder al tipo de la columna (vector -> vector_cosine_ops, halfvec -> halfvec_cosine_ops). La lee db/06_indices_concurrentes.sql.';
