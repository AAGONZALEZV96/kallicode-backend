-- kc:sin-transaccion
-- ============================================================================
-- KALLICODE · 071_retiro_indices_no_usados.sql
-- KC-DB-12-4 — Revisión final del plan de índices y retirada de los no usados.
-- ============================================================================

-- Redundantes sobre tablas ordinarias (CONCURRENTLY)
DROP INDEX CONCURRENTLY IF EXISTS core.idx_gate_signatures_tenant_ticket_id;
DROP INDEX CONCURRENTLY IF EXISTS core.idx_llm_steps_tenant_ticket_id;
DROP INDEX CONCURRENTLY IF EXISTS core.idx_qa_runs_tenant_ticket_id;
DROP INDEX CONCURRENTLY IF EXISTS core.spec_criteria_tenant_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.idx_specs_tenant_ticket_id;

-- Sin consulta ni restricción dependiente sobre tablas ordinarias (CONCURRENTLY)
DROP INDEX CONCURRENTLY IF EXISTS cm.nodes_kind_idx;
DROP INDEX CONCURRENTLY IF EXISTS core.audit_exports_tenant_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.escalations_abiertas_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.gates_tenant_actor_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.llm_steps_tenant_job_ix;
DROP INDEX CONCURRENTLY IF EXISTS core.refresh_tokens_tenant_user_activos_ix;

-- Índices padre de tablas particionadas (PostgreSQL prohíbe CONCURRENTLY en particionadas)
DROP INDEX IF EXISTS core.idx_usage_tokens_tenant_ticket_id;
DROP INDEX IF EXISTS core.job_transitions_tenant_job_ix;