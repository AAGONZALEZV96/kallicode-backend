-- kc:sin-transaccion
-- ============================================================================
-- KALLICODE · 032_indices_tenant_first.sql
-- KC-DB-06-2 (DB-F26) — Convención tenant-first aplicada a los índices que la incumplen.
--
-- db/02_indexes.sql declaraba «todo índice multi-tenant comienza por tenant_id», para que el
-- filtro implícito de RLS (tenant_id = core.current_tenant()) sea barato: con tenant_id como
-- primera columna el planificador lo resuelve DENTRO del índice. Un índice que no empieza por
-- tenant_id obliga a filtrar por la política DESPUÉS de la búsqueda.
--
-- Esta migración reescribe los 17 índices de acceso que la incumplían y que admiten la
-- convención (los demás son excepciones documentadas en docs/plan-indices.md):
--
--   * 11 se reescriben con tenant_id como primera columna, conservando el resto del orden.
--   * 6 ya tienen un equivalente tenant-first (los índices de FK de la migración 018, con las
--     mismas columnas): reescribirlos produciría un duplicado, así que solo se retiran.
--
-- Sin ventana sin índice: primero se crean (CONCURRENTLY) todos los nuevos y solo después se
-- retiran (CONCURRENTLY) los viejos. Por eso el archivo va sin transacción; cada sentencia es
-- re-ejecutable (IF NOT EXISTS / IF EXISTS).
--
-- Si un CREATE INDEX CONCURRENTLY falla a mitad deja un índice INVÁLIDO con ese nombre y el
-- IF NOT EXISTS de una nueva ejecución lo daría por bueno: antes de reintentar hay que borrarlo
-- (DROP INDEX CONCURRENTLY <nombre>). test_convencion_indices.py comprueba que no queden índices
-- inválidos.
--
-- Nota: el ticket nombra esta migración 019, pero 019 ya existe.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Índices nuevos, tenant-first.
-- ---------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY IF NOT EXISTS gates_tenant_ticket_ix
    ON core.gate_signatures (tenant_id, ticket_id, gate, creado_en);
CREATE INDEX CONCURRENTLY IF NOT EXISTS gates_tenant_actor_ix
    ON core.gate_signatures (tenant_id, actor_id, creado_en DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS job_transitions_tenant_job_ix
    ON core.job_transitions (tenant_id, job_id, creado_en);
CREATE INDEX CONCURRENTLY IF NOT EXISTS llm_steps_tenant_job_ix
    ON core.llm_steps (tenant_id, job_id, creado_en);
CREATE INDEX CONCURRENTLY IF NOT EXISTS llm_steps_tenant_ticket_etapa_ix
    ON core.llm_steps (tenant_id, ticket_id, etapa);
CREATE INDEX CONCURRENTLY IF NOT EXISTS notifications_tenant_user_ix
    ON core.notifications (tenant_id, user_id, creada_en DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS notifications_tenant_noleidas_ix
    ON core.notifications (tenant_id, user_id) WHERE NOT leida;
CREATE INDEX CONCURRENTLY IF NOT EXISTS refresh_tokens_tenant_user_activos_ix
    ON core.refresh_tokens (tenant_id, user_id) WHERE usado_en IS NULL AND NOT revocado;
CREATE INDEX CONCURRENTLY IF NOT EXISTS qa_runs_tenant_ticket_ix
    ON core.qa_runs (tenant_id, ticket_id, corrida DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS specs_tenant_ticket_ix
    ON core.specs (tenant_id, ticket_id, version DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS usage_tokens_tenant_ticket_etapa_ix
    ON core.usage_tokens (tenant_id, ticket_id, etapa);

-- ---------------------------------------------------------------------------
-- 2. Retirada de los índices reemplazados (ya existe su sucesor tenant-first).
-- ---------------------------------------------------------------------------
DROP INDEX CONCURRENTLY IF EXISTS core.gates_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.gates_actor_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.job_transitions_job_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.llm_steps_job_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.llm_steps_ticket_etapa_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.notifications_user_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.notifications_noleidas_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.refresh_tokens_user_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.qa_runs_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.specs_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.usage_tokens_ticket_ix;

-- ---------------------------------------------------------------------------
-- 3. Retirada de los índices cuyo equivalente tenant-first YA existe (migración 018):
--      attachments_ticket_ix  (ticket_id)     -> idx_ticket_attachments_tenant_ticket_id
--      findings_ticket_ix     (ticket_id)     -> idx_security_findings_tenant_ticket_id
--      usage_loc_ticket_ix    (ticket_id)     -> idx_usage_loc_tenant_ticket_id
--      watchers_user_ix       (user_id)       -> idx_ticket_watchers_tenant_user_id
--      tickets_reporter_ix    (reportado_por) -> idx_tickets_tenant_reportado_por
--      tickets_duplicado_ix   (duplicado_de, parcial) -> idx_tickets_tenant_duplicado_de
-- ---------------------------------------------------------------------------
DROP INDEX CONCURRENTLY IF EXISTS core.attachments_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.findings_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.usage_loc_ticket_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.watchers_user_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.tickets_reporter_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.tickets_duplicado_ix;
