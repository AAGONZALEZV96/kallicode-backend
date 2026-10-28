-- ============================================================================
-- KALLICODE · 015_fks_compuestas_validate.sql
-- KC-DB-03-2 (DB-F01) — Paso 2/3: VALIDATE de las FK compuestas de 014.
--
-- Revisa las filas existentes sin bloquear escrituras (SHARE UPDATE EXCLUSIVE
-- en la hija). Va en archivo aparte para que un fallo aquí no deshaga 014, que
-- ya protege toda escritura nueva. Si falla, hay filas de un tenant colgadas
-- de otro: listarlas con
--   SELECT * FROM core.incoherencias_tenant();
-- repararlas y volver a ejecutar el runner (VALIDATE es reejecutable).
-- ============================================================================
-- kc:sin-transaccion

SET lock_timeout = '5s';
SET statement_timeout = '30min';

ALTER TABLE cm.concepts VALIDATE CONSTRAINT concepts_curado_por_fkey_t;
ALTER TABLE cm.curation_queue VALIDATE CONSTRAINT curation_queue_revisado_por_fkey_t;
ALTER TABLE cm.incidents VALIDATE CONSTRAINT incidents_ticket_id_fkey_t;
ALTER TABLE core.audit_exports VALIDATE CONSTRAINT audit_exports_solicitado_por_fkey_t;
ALTER TABLE core.audit_exports VALIDATE CONSTRAINT audit_exports_ticket_id_fkey_t;
ALTER TABLE core.escalations VALIDATE CONSTRAINT escalations_job_id_fkey_t;
ALTER TABLE core.gate_signatures VALIDATE CONSTRAINT gate_signatures_actor_id_fkey_t;
ALTER TABLE core.gate_signatures VALIDATE CONSTRAINT gate_signatures_spec_id_fkey_t;
ALTER TABLE core.gate_signatures VALIDATE CONSTRAINT gate_signatures_ticket_id_fkey_t;
ALTER TABLE core.invitations VALIDATE CONSTRAINT invitations_invitado_por_fkey_t;
ALTER TABLE core.jobs VALIDATE CONSTRAINT jobs_ticket_id_fkey_t;
ALTER TABLE core.job_transitions VALIDATE CONSTRAINT job_transitions_job_id_fkey_t;
ALTER TABLE core.llm_steps VALIDATE CONSTRAINT llm_steps_job_id_fkey_t;
ALTER TABLE core.llm_steps VALIDATE CONSTRAINT llm_steps_ticket_id_fkey_t;
ALTER TABLE core.notifications VALIDATE CONSTRAINT notifications_ticket_id_fkey_t;
ALTER TABLE core.notifications VALIDATE CONSTRAINT notifications_user_id_fkey_t;
ALTER TABLE core.onboarding_documents VALIDATE CONSTRAINT onboarding_documents_subido_por_fkey_t;
ALTER TABLE core.password_reset_tokens VALIDATE CONSTRAINT password_reset_tokens_user_id_fkey_t;
ALTER TABLE core.production_lines VALIDATE CONSTRAINT lines_job_fk_t;
ALTER TABLE core.qa_results VALIDATE CONSTRAINT qa_results_qa_run_id_fkey_t;
ALTER TABLE core.qa_runs VALIDATE CONSTRAINT qa_runs_spec_id_fkey_t;
ALTER TABLE core.qa_runs VALIDATE CONSTRAINT qa_runs_ticket_id_fkey_t;
ALTER TABLE core.refresh_tokens VALIDATE CONSTRAINT refresh_tokens_user_id_fkey_t;
ALTER TABLE core.security_findings VALIDATE CONSTRAINT security_findings_ticket_id_fkey_t;
ALTER TABLE core.spec_alternatives VALIDATE CONSTRAINT spec_alternatives_spec_id_fkey_t;
ALTER TABLE core.spec_criteria VALIDATE CONSTRAINT spec_criteria_spec_id_fkey_t;
ALTER TABLE core.specs VALIDATE CONSTRAINT specs_ticket_id_fkey_t;
ALTER TABLE core.ticket_attachments VALIDATE CONSTRAINT ticket_attachments_subido_por_fkey_t;
ALTER TABLE core.ticket_attachments VALIDATE CONSTRAINT ticket_attachments_ticket_id_fkey_t;
ALTER TABLE core.tickets VALIDATE CONSTRAINT tickets_cancelado_por_fkey_t;
ALTER TABLE core.tickets VALIDATE CONSTRAINT tickets_duplicado_de_fkey_t;
ALTER TABLE core.tickets VALIDATE CONSTRAINT tickets_reportado_por_fkey_t;
ALTER TABLE core.ticket_watchers VALIDATE CONSTRAINT ticket_watchers_ticket_id_fkey_t;
ALTER TABLE core.ticket_watchers VALIDATE CONSTRAINT ticket_watchers_user_id_fkey_t;
ALTER TABLE core.upgrade_requests VALIDATE CONSTRAINT upgrade_requests_solicitado_por_fkey_t;
ALTER TABLE core.usage_loc VALIDATE CONSTRAINT usage_loc_ticket_id_fkey_t;
ALTER TABLE core.usage_tokens VALIDATE CONSTRAINT usage_tokens_ticket_id_fkey_t;
ALTER TABLE core.webhook_inbox VALIDATE CONSTRAINT webhook_inbox_connection_id_fkey_t;
ALTER TABLE core.webhook_inbox VALIDATE CONSTRAINT webhook_inbox_ticket_id_fkey_t;
ALTER TABLE vec.doc_chunks VALIDATE CONSTRAINT doc_chunks_documento_id_fkey_t;
ALTER TABLE vec.ticket_embeddings VALIDATE CONSTRAINT ticket_embeddings_ticket_id_fkey_t;

RESET lock_timeout;
RESET statement_timeout;
