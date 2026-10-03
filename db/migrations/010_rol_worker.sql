-- ============================================================================
-- KALLICODE · 010_rol_worker.sql
-- KC-DB-02-2 (DB-F11) — Rol kallicode_worker y políticas para las colas
-- cross-tenant.
--
-- Los workers barren colas de TODOS los tenants (normalizador, planificador,
-- purgas), pero RLS solo deja ver un tenant por sesión. Hasta ahora usaban
-- kallicode_admin (BYPASSRLS): un defecto en un worker exponía todo el esquema.
--
-- kallicode_worker ve TODOS los tenants solo en estas 5 tablas, por política
-- (no por BYPASSRLS), y nada en el resto: sin GRANT, cualquier otra tabla
-- responde "permission denied".
--
--   Tabla                   Índice que sirve el barrido   Operaciones
--   core.webhook_inbox      webhook_inbox_pend_ix         SELECT, UPDATE, DELETE
--   core.jobs               jobs_pendientes_ix            SELECT
--   core.refresh_tokens     refresh_tokens_expira_ix      SELECT, DELETE
--   core.ticket_attachments attachments_ticket_ix (*)     SELECT, DELETE
--   core.idempotency_keys   idempotency_purga_ix          SELECT, DELETE
--
--   (*) El planificador prefiere attachments_ticket_ix (ticket_id IS NULL)
--       a attachments_huerfanos_ix, que queda redundante (KC-DB-06-3).
--
-- Sin BEGIN/COMMIT: el runner abre la transacción (db/migrations/README.md).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Privilegios mínimos: solo las operaciones que usan los workers.
--    (UPDATE en webhook_inbox también lo exige el SELECT ... FOR UPDATE
--    SKIP LOCKED del normalizador.)
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA core TO kallicode_worker;

GRANT SELECT, UPDATE, DELETE ON core.webhook_inbox      TO kallicode_worker;
GRANT SELECT                 ON core.jobs               TO kallicode_worker;
GRANT SELECT, DELETE         ON core.refresh_tokens     TO kallicode_worker;
GRANT SELECT, DELETE         ON core.ticket_attachments TO kallicode_worker;
GRANT SELECT, DELETE         ON core.idempotency_keys   TO kallicode_worker;

-- ----------------------------------------------------------------------------
-- 2. Políticas: las políticas permisivas de una tabla se combinan con OR. A
--    tenant_isolation ("solo tu tenant") se suma esta, que deja ver todos los
--    tenants SOLO a kallicode_worker y SOLO en estas tablas. Las operaciones
--    concretas las limita el GRANT de arriba, no la política.
-- ----------------------------------------------------------------------------
CREATE POLICY worker_colas ON core.webhook_inbox
    FOR ALL TO kallicode_worker USING (true);
CREATE POLICY worker_colas ON core.jobs
    FOR ALL TO kallicode_worker USING (true);
CREATE POLICY worker_colas ON core.refresh_tokens
    FOR ALL TO kallicode_worker USING (true);
CREATE POLICY worker_colas ON core.ticket_attachments
    FOR ALL TO kallicode_worker USING (true);
CREATE POLICY worker_colas ON core.idempotency_keys
    FOR ALL TO kallicode_worker USING (true);
