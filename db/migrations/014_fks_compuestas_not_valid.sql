-- ============================================================================
-- KALLICODE · 014_fks_compuestas_not_valid.sql
-- KC-DB-03-2 (DB-F01) — Paso 1/3: FK compuestas con tenant_id, NOT VALID.
--
-- Cada FK escalar hija.x -> padre(id) recibe una gemela compuesta
-- (tenant_id, x) -> padre(tenant_id, id): la base rechaza que una fila cuelgue
-- de un padre de OTRO tenant. NOT VALID no revisa las filas existentes (eso es
-- 015) pero protege desde ya toda escritura nueva. Es solo catálogo: el
-- bloqueo dura un instante; lock_timeout evita encolarse tras una transacción
-- larga. Requiere 012 (UNIQUE (tenant_id, id) en los padres).
--
-- Generado desde pg_constraint; conserva ON DELETE y el modo diferido de cada
-- FK. ON DELETE SET NULL pasa a SET NULL (col): en una FK compuesta, SET NULL
-- vaciaría también tenant_id, que es NOT NULL. Las 41 FK:
--   cm.concepts.curado_por -> core.users
--   cm.curation_queue.revisado_por -> core.users
--   cm.incidents.ticket_id -> core.tickets
--   core.audit_exports.solicitado_por -> core.users
--   core.audit_exports.ticket_id -> core.tickets
--   core.escalations.job_id -> core.jobs ON DELETE CASCADE
--   core.gate_signatures.actor_id -> core.users
--   core.gate_signatures.spec_id -> core.specs
--   core.gate_signatures.ticket_id -> core.tickets ON DELETE CASCADE
--   core.invitations.invitado_por -> core.users
--   core.jobs.ticket_id -> core.tickets ON DELETE CASCADE
--   core.job_transitions.job_id -> core.jobs ON DELETE CASCADE
--   core.llm_steps.job_id -> core.jobs ON DELETE CASCADE
--   core.llm_steps.ticket_id -> core.tickets ON DELETE CASCADE
--   core.notifications.ticket_id -> core.tickets ON DELETE CASCADE
--   core.notifications.user_id -> core.users ON DELETE CASCADE
--   core.onboarding_documents.subido_por -> core.users
--   core.password_reset_tokens.user_id -> core.users ON DELETE CASCADE
--   core.production_lines.job_id -> core.jobs DEFERRABLE INITIALLY DEFERRED
--   core.qa_results.qa_run_id -> core.qa_runs ON DELETE CASCADE
--   core.qa_runs.spec_id -> core.specs
--   core.qa_runs.ticket_id -> core.tickets ON DELETE CASCADE
--   core.refresh_tokens.user_id -> core.users ON DELETE CASCADE
--   core.security_findings.ticket_id -> core.tickets ON DELETE CASCADE
--   core.spec_alternatives.spec_id -> core.specs ON DELETE CASCADE
--   core.spec_criteria.spec_id -> core.specs ON DELETE CASCADE
--   core.specs.ticket_id -> core.tickets ON DELETE CASCADE
--   core.ticket_attachments.subido_por -> core.users
--   core.ticket_attachments.ticket_id -> core.tickets ON DELETE CASCADE
--   core.tickets.cancelado_por -> core.users
--   core.tickets.duplicado_de -> core.tickets
--   core.tickets.reportado_por -> core.users
--   core.ticket_watchers.ticket_id -> core.tickets ON DELETE CASCADE
--   core.ticket_watchers.user_id -> core.users ON DELETE CASCADE
--   core.upgrade_requests.solicitado_por -> core.users
--   core.usage_loc.ticket_id -> core.tickets ON DELETE SET NULL (ticket_id)
--   core.usage_tokens.ticket_id -> core.tickets ON DELETE CASCADE
--   core.webhook_inbox.connection_id -> core.connections ON DELETE CASCADE
--   core.webhook_inbox.ticket_id -> core.tickets
--   vec.doc_chunks.documento_id -> core.onboarding_documents ON DELETE CASCADE
--   vec.ticket_embeddings.ticket_id -> core.tickets ON DELETE CASCADE
--
-- Las gemelas llevan el sufijo _t; 016 retira las escalares y les devuelve
-- el nombre original. Sin BEGIN/COMMIT: la transacción la abre el runner.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '1min';

ALTER TABLE cm.concepts
    ADD CONSTRAINT concepts_curado_por_fkey_t
    FOREIGN KEY (tenant_id, curado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE cm.curation_queue
    ADD CONSTRAINT curation_queue_revisado_por_fkey_t
    FOREIGN KEY (tenant_id, revisado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE cm.incidents
    ADD CONSTRAINT incidents_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) NOT VALID;

ALTER TABLE core.audit_exports
    ADD CONSTRAINT audit_exports_solicitado_por_fkey_t
    FOREIGN KEY (tenant_id, solicitado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.audit_exports
    ADD CONSTRAINT audit_exports_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) NOT VALID;

ALTER TABLE core.escalations
    ADD CONSTRAINT escalations_job_id_fkey_t
    FOREIGN KEY (tenant_id, job_id)
    REFERENCES core.jobs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gate_signatures_actor_id_fkey_t
    FOREIGN KEY (tenant_id, actor_id)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gate_signatures_spec_id_fkey_t
    FOREIGN KEY (tenant_id, spec_id)
    REFERENCES core.specs (tenant_id, id) NOT VALID;

ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gate_signatures_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.invitations
    ADD CONSTRAINT invitations_invitado_por_fkey_t
    FOREIGN KEY (tenant_id, invitado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.jobs
    ADD CONSTRAINT jobs_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.job_transitions
    ADD CONSTRAINT job_transitions_job_id_fkey_t
    FOREIGN KEY (tenant_id, job_id)
    REFERENCES core.jobs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.llm_steps
    ADD CONSTRAINT llm_steps_job_id_fkey_t
    FOREIGN KEY (tenant_id, job_id)
    REFERENCES core.jobs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.llm_steps
    ADD CONSTRAINT llm_steps_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.notifications
    ADD CONSTRAINT notifications_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.notifications
    ADD CONSTRAINT notifications_user_id_fkey_t
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.users (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.onboarding_documents
    ADD CONSTRAINT onboarding_documents_subido_por_fkey_t
    FOREIGN KEY (tenant_id, subido_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey_t
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.users (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.production_lines
    ADD CONSTRAINT lines_job_fk_t
    FOREIGN KEY (tenant_id, job_id)
    REFERENCES core.jobs (tenant_id, id) DEFERRABLE INITIALLY DEFERRED NOT VALID;

ALTER TABLE core.qa_results
    ADD CONSTRAINT qa_results_qa_run_id_fkey_t
    FOREIGN KEY (tenant_id, qa_run_id)
    REFERENCES core.qa_runs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.qa_runs
    ADD CONSTRAINT qa_runs_spec_id_fkey_t
    FOREIGN KEY (tenant_id, spec_id)
    REFERENCES core.specs (tenant_id, id) NOT VALID;

ALTER TABLE core.qa_runs
    ADD CONSTRAINT qa_runs_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey_t
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.users (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.security_findings
    ADD CONSTRAINT security_findings_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.spec_alternatives
    ADD CONSTRAINT spec_alternatives_spec_id_fkey_t
    FOREIGN KEY (tenant_id, spec_id)
    REFERENCES core.specs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.spec_criteria
    ADD CONSTRAINT spec_criteria_spec_id_fkey_t
    FOREIGN KEY (tenant_id, spec_id)
    REFERENCES core.specs (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.specs
    ADD CONSTRAINT specs_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT ticket_attachments_subido_por_fkey_t
    FOREIGN KEY (tenant_id, subido_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT ticket_attachments_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.tickets
    ADD CONSTRAINT tickets_cancelado_por_fkey_t
    FOREIGN KEY (tenant_id, cancelado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.tickets
    ADD CONSTRAINT tickets_duplicado_de_fkey_t
    FOREIGN KEY (tenant_id, duplicado_de)
    REFERENCES core.tickets (tenant_id, id) NOT VALID;

ALTER TABLE core.tickets
    ADD CONSTRAINT tickets_reportado_por_fkey_t
    FOREIGN KEY (tenant_id, reportado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.ticket_watchers
    ADD CONSTRAINT ticket_watchers_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.ticket_watchers
    ADD CONSTRAINT ticket_watchers_user_id_fkey_t
    FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.users (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.upgrade_requests
    ADD CONSTRAINT upgrade_requests_solicitado_por_fkey_t
    FOREIGN KEY (tenant_id, solicitado_por)
    REFERENCES core.users (tenant_id, id) NOT VALID;

ALTER TABLE core.usage_loc
    ADD CONSTRAINT usage_loc_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE SET NULL (ticket_id) NOT VALID;

ALTER TABLE core.usage_tokens
    ADD CONSTRAINT usage_tokens_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.webhook_inbox
    ADD CONSTRAINT webhook_inbox_connection_id_fkey_t
    FOREIGN KEY (tenant_id, connection_id)
    REFERENCES core.connections (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE core.webhook_inbox
    ADD CONSTRAINT webhook_inbox_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) NOT VALID;

ALTER TABLE vec.doc_chunks
    ADD CONSTRAINT doc_chunks_documento_id_fkey_t
    FOREIGN KEY (tenant_id, documento_id)
    REFERENCES core.onboarding_documents (tenant_id, id) ON DELETE CASCADE NOT VALID;

ALTER TABLE vec.ticket_embeddings
    ADD CONSTRAINT ticket_embeddings_ticket_id_fkey_t
    FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE NOT VALID;
