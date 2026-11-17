-- ============================================================================
-- KALLICODE · 045_refresco_presupuesto_docs.sql
-- KC-CM-08-5 (refresco y purga documental) y KC-CM-08-6 (presupuesto documental).
--
--   1. core.onboarding_documents.eliminado_en: baja lógica. El refresco purga los chunks
--      (vec.doc_chunks) de un documento dado de baja y, por las FK en cascada de la 040,
--      sus enlaces al grafo (cm.doc_chunk_links), todo en una transacción. Al purgar también se
--      libera hash_contenido para que el mismo contenido pueda volver a subirse.
--   2. core.usage_tokens: el coste de la ingesta documental no pertenece a ningún ticket.
--      ticket_id pasa a admitir NULL y aparecen dos columnas:
--        origen       'pipeline' (por defecto; el consumo de tickets de siempre),
--                     'codemapping_codigo' o 'codemapping_docs'.
--        snapshot_id  corrida de CodeMapping que generó el consumo (opcional).
--      El CHECK ut_origen_ticket_chk mantiene la regla vieja para el pipeline: sin ticket no hay
--      fila de origen 'pipeline'. La FK compuesta (tenant_id, ticket_id) -> tickets sigue vigente:
--      con ticket_id NULL no se evalúa (MATCH SIMPLE).
--
-- Aditiva y reejecutable. No toca filas existentes: todas quedan con origen 'pipeline'.
-- No crea índices: ninguna consulta los usa todavía (docs/plan-indices.md exige un consumidor).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- 1. Baja lógica de documentos
ALTER TABLE core.onboarding_documents
    ADD COLUMN IF NOT EXISTS eliminado_en timestamptz;

-- 2. Consumo de tokens que no pertenece a un ticket
ALTER TABLE core.usage_tokens ALTER COLUMN ticket_id DROP NOT NULL;

ALTER TABLE core.usage_tokens
    ADD COLUMN IF NOT EXISTS origen      text NOT NULL DEFAULT 'pipeline',
    ADD COLUMN IF NOT EXISTS snapshot_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'core.usage_tokens'::regclass AND conname = 'ut_origen_chk'
    ) THEN
        ALTER TABLE core.usage_tokens
            ADD CONSTRAINT ut_origen_chk
                CHECK (origen IN ('pipeline', 'codemapping_codigo', 'codemapping_docs')),
            ADD CONSTRAINT ut_origen_ticket_chk
                CHECK (origen <> 'pipeline' OR ticket_id IS NOT NULL);
    END IF;
END $$;

-- La ingesta corre como kallicode_worker además de kallicode_app (GRANT de la 035 solo cubre a la app).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_worker') THEN
        GRANT SELECT, INSERT ON core.usage_tokens TO kallicode_worker;
    END IF;
END $$;
