-- ============================================================================
-- KALLICODE · 016_fks_retirar_escalares.sql
-- KC-DB-03-2 (DB-F01) — Paso 3/3: retirar las FK escalares.
--
-- Validadas las compuestas (015), las escalares sobran: se eliminan y cada
-- compuesta recupera el nombre original (sin el sufijo _t), así los mensajes
-- de error y cualquier referencia por nombre no cambian. DROP CONSTRAINT toma
-- un bloqueo exclusivo de un instante; todo va en una transacción con
-- lock_timeout: o se aplica entero o nada.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '1min';

ALTER TABLE cm.concepts DROP CONSTRAINT concepts_curado_por_fkey;
ALTER TABLE cm.concepts RENAME CONSTRAINT concepts_curado_por_fkey_t TO concepts_curado_por_fkey;

ALTER TABLE cm.curation_queue DROP CONSTRAINT curation_queue_revisado_por_fkey;
ALTER TABLE cm.curation_queue RENAME CONSTRAINT curation_queue_revisado_por_fkey_t TO curation_queue_revisado_por_fkey;

ALTER TABLE cm.incidents DROP CONSTRAINT incidents_ticket_id_fkey;
ALTER TABLE cm.incidents RENAME CONSTRAINT incidents_ticket_id_fkey_t TO incidents_ticket_id_fkey;

ALTER TABLE core.audit_exports DROP CONSTRAINT audit_exports_solicitado_por_fkey;
ALTER TABLE core.audit_exports RENAME CONSTRAINT audit_exports_solicitado_por_fkey_t TO audit_exports_solicitado_por_fkey;

ALTER TABLE core.audit_exports DROP CONSTRAINT audit_exports_ticket_id_fkey;
ALTER TABLE core.audit_exports RENAME CONSTRAINT audit_exports_ticket_id_fkey_t TO audit_exports_ticket_id_fkey;

ALTER TABLE core.escalations DROP CONSTRAINT escalations_job_id_fkey;
ALTER TABLE core.escalations RENAME CONSTRAINT escalations_job_id_fkey_t TO escalations_job_id_fkey;

ALTER TABLE core.gate_signatures DROP CONSTRAINT gate_signatures_actor_id_fkey;
ALTER TABLE core.gate_signatures RENAME CONSTRAINT gate_signatures_actor_id_fkey_t TO gate_signatures_actor_id_fkey;

ALTER TABLE core.gate_signatures DROP CONSTRAINT gate_signatures_spec_id_fkey;
ALTER TABLE core.gate_signatures RENAME CONSTRAINT gate_signatures_spec_id_fkey_t TO gate_signatures_spec_id_fkey;

ALTER TABLE core.gate_signatures DROP CONSTRAINT gate_signatures_ticket_id_fkey;
ALTER TABLE core.gate_signatures RENAME CONSTRAINT gate_signatures_ticket_id_fkey_t TO gate_signatures_ticket_id_fkey;

ALTER TABLE core.invitations DROP CONSTRAINT invitations_invitado_por_fkey;
ALTER TABLE core.invitations RENAME CONSTRAINT invitations_invitado_por_fkey_t TO invitations_invitado_por_fkey;

ALTER TABLE core.jobs DROP CONSTRAINT jobs_ticket_id_fkey;
ALTER TABLE core.jobs RENAME CONSTRAINT jobs_ticket_id_fkey_t TO jobs_ticket_id_fkey;

ALTER TABLE core.job_transitions DROP CONSTRAINT job_transitions_job_id_fkey;
ALTER TABLE core.job_transitions RENAME CONSTRAINT job_transitions_job_id_fkey_t TO job_transitions_job_id_fkey;

ALTER TABLE core.llm_steps DROP CONSTRAINT llm_steps_job_id_fkey;
ALTER TABLE core.llm_steps RENAME CONSTRAINT llm_steps_job_id_fkey_t TO llm_steps_job_id_fkey;

ALTER TABLE core.llm_steps DROP CONSTRAINT llm_steps_ticket_id_fkey;
ALTER TABLE core.llm_steps RENAME CONSTRAINT llm_steps_ticket_id_fkey_t TO llm_steps_ticket_id_fkey;

ALTER TABLE core.notifications DROP CONSTRAINT notifications_ticket_id_fkey;
ALTER TABLE core.notifications RENAME CONSTRAINT notifications_ticket_id_fkey_t TO notifications_ticket_id_fkey;

ALTER TABLE core.notifications DROP CONSTRAINT notifications_user_id_fkey;
ALTER TABLE core.notifications RENAME CONSTRAINT notifications_user_id_fkey_t TO notifications_user_id_fkey;

ALTER TABLE core.onboarding_documents DROP CONSTRAINT onboarding_documents_subido_por_fkey;
ALTER TABLE core.onboarding_documents RENAME CONSTRAINT onboarding_documents_subido_por_fkey_t TO onboarding_documents_subido_por_fkey;

ALTER TABLE core.password_reset_tokens DROP CONSTRAINT password_reset_tokens_user_id_fkey;
ALTER TABLE core.password_reset_tokens RENAME CONSTRAINT password_reset_tokens_user_id_fkey_t TO password_reset_tokens_user_id_fkey;

ALTER TABLE core.production_lines DROP CONSTRAINT lines_job_fk;
ALTER TABLE core.production_lines RENAME CONSTRAINT lines_job_fk_t TO lines_job_fk;

ALTER TABLE core.qa_results DROP CONSTRAINT qa_results_qa_run_id_fkey;
ALTER TABLE core.qa_results RENAME CONSTRAINT qa_results_qa_run_id_fkey_t TO qa_results_qa_run_id_fkey;

ALTER TABLE core.qa_runs DROP CONSTRAINT qa_runs_spec_id_fkey;
ALTER TABLE core.qa_runs RENAME CONSTRAINT qa_runs_spec_id_fkey_t TO qa_runs_spec_id_fkey;

ALTER TABLE core.qa_runs DROP CONSTRAINT qa_runs_ticket_id_fkey;
ALTER TABLE core.qa_runs RENAME CONSTRAINT qa_runs_ticket_id_fkey_t TO qa_runs_ticket_id_fkey;

ALTER TABLE core.refresh_tokens DROP CONSTRAINT refresh_tokens_user_id_fkey;
ALTER TABLE core.refresh_tokens RENAME CONSTRAINT refresh_tokens_user_id_fkey_t TO refresh_tokens_user_id_fkey;

ALTER TABLE core.security_findings DROP CONSTRAINT security_findings_ticket_id_fkey;
ALTER TABLE core.security_findings RENAME CONSTRAINT security_findings_ticket_id_fkey_t TO security_findings_ticket_id_fkey;

ALTER TABLE core.spec_alternatives DROP CONSTRAINT spec_alternatives_spec_id_fkey;
ALTER TABLE core.spec_alternatives RENAME CONSTRAINT spec_alternatives_spec_id_fkey_t TO spec_alternatives_spec_id_fkey;

ALTER TABLE core.spec_criteria DROP CONSTRAINT spec_criteria_spec_id_fkey;
ALTER TABLE core.spec_criteria RENAME CONSTRAINT spec_criteria_spec_id_fkey_t TO spec_criteria_spec_id_fkey;

ALTER TABLE core.specs DROP CONSTRAINT specs_ticket_id_fkey;
ALTER TABLE core.specs RENAME CONSTRAINT specs_ticket_id_fkey_t TO specs_ticket_id_fkey;

ALTER TABLE core.ticket_attachments DROP CONSTRAINT ticket_attachments_subido_por_fkey;
ALTER TABLE core.ticket_attachments RENAME CONSTRAINT ticket_attachments_subido_por_fkey_t TO ticket_attachments_subido_por_fkey;

ALTER TABLE core.ticket_attachments DROP CONSTRAINT ticket_attachments_ticket_id_fkey;
ALTER TABLE core.ticket_attachments RENAME CONSTRAINT ticket_attachments_ticket_id_fkey_t TO ticket_attachments_ticket_id_fkey;

ALTER TABLE core.tickets DROP CONSTRAINT tickets_cancelado_por_fkey;
ALTER TABLE core.tickets RENAME CONSTRAINT tickets_cancelado_por_fkey_t TO tickets_cancelado_por_fkey;

ALTER TABLE core.tickets DROP CONSTRAINT tickets_duplicado_de_fkey;
ALTER TABLE core.tickets RENAME CONSTRAINT tickets_duplicado_de_fkey_t TO tickets_duplicado_de_fkey;

ALTER TABLE core.tickets DROP CONSTRAINT tickets_reportado_por_fkey;
ALTER TABLE core.tickets RENAME CONSTRAINT tickets_reportado_por_fkey_t TO tickets_reportado_por_fkey;

ALTER TABLE core.ticket_watchers DROP CONSTRAINT ticket_watchers_ticket_id_fkey;
ALTER TABLE core.ticket_watchers RENAME CONSTRAINT ticket_watchers_ticket_id_fkey_t TO ticket_watchers_ticket_id_fkey;

ALTER TABLE core.ticket_watchers DROP CONSTRAINT ticket_watchers_user_id_fkey;
ALTER TABLE core.ticket_watchers RENAME CONSTRAINT ticket_watchers_user_id_fkey_t TO ticket_watchers_user_id_fkey;

ALTER TABLE core.upgrade_requests DROP CONSTRAINT upgrade_requests_solicitado_por_fkey;
ALTER TABLE core.upgrade_requests RENAME CONSTRAINT upgrade_requests_solicitado_por_fkey_t TO upgrade_requests_solicitado_por_fkey;

ALTER TABLE core.usage_loc DROP CONSTRAINT usage_loc_ticket_id_fkey;
ALTER TABLE core.usage_loc RENAME CONSTRAINT usage_loc_ticket_id_fkey_t TO usage_loc_ticket_id_fkey;

ALTER TABLE core.usage_tokens DROP CONSTRAINT usage_tokens_ticket_id_fkey;
ALTER TABLE core.usage_tokens RENAME CONSTRAINT usage_tokens_ticket_id_fkey_t TO usage_tokens_ticket_id_fkey;

ALTER TABLE core.webhook_inbox DROP CONSTRAINT webhook_inbox_connection_id_fkey;
ALTER TABLE core.webhook_inbox RENAME CONSTRAINT webhook_inbox_connection_id_fkey_t TO webhook_inbox_connection_id_fkey;

ALTER TABLE core.webhook_inbox DROP CONSTRAINT webhook_inbox_ticket_id_fkey;
ALTER TABLE core.webhook_inbox RENAME CONSTRAINT webhook_inbox_ticket_id_fkey_t TO webhook_inbox_ticket_id_fkey;

ALTER TABLE vec.doc_chunks DROP CONSTRAINT doc_chunks_documento_id_fkey;
ALTER TABLE vec.doc_chunks RENAME CONSTRAINT doc_chunks_documento_id_fkey_t TO doc_chunks_documento_id_fkey;

ALTER TABLE vec.ticket_embeddings DROP CONSTRAINT ticket_embeddings_ticket_id_fkey;
ALTER TABLE vec.ticket_embeddings RENAME CONSTRAINT ticket_embeddings_ticket_id_fkey_t TO ticket_embeddings_ticket_id_fkey;
