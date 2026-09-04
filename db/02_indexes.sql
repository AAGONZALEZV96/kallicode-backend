-- ============================================================================
-- KALLICODE · 02_indexes.sql — Índices de rendimiento
-- (Los índices UNIQUE que implementan reglas de negocio viven en 01_schema.sql
--  junto a su tabla; aquí van los índices de acceso.)
-- Convención: todo índice multi-tenant comienza por tenant_id.
-- ============================================================================

BEGIN;

-- --- Usuarios y sesiones ----------------------------------------------------
CREATE INDEX users_tenant_rol_ix        ON core.users (tenant_id, rol) WHERE estado = 'activo';
CREATE INDEX users_tenant_estado_ix     ON core.users (tenant_id, estado);
CREATE INDEX refresh_tokens_user_ix     ON core.refresh_tokens (user_id) WHERE usado_en IS NULL AND NOT revocado;
CREATE INDEX refresh_tokens_expira_ix   ON core.refresh_tokens (expira_en);   -- purga programada
CREATE INDEX invitations_tenant_ix      ON core.invitations (tenant_id, estado);

-- --- Tickets: listados, filtros y búsqueda ----------------------------------
-- Listado por defecto (actualizado_en desc) con filtros:
CREATE INDEX tickets_tenant_upd_ix      ON core.tickets (tenant_id, actualizado_en DESC);
CREATE INDEX tickets_tenant_etapa_ix    ON core.tickets (tenant_id, etapa, actualizado_en DESC);
CREATE INDEX tickets_tenant_gate_ix     ON core.tickets (tenant_id, gate_pendiente)
    WHERE gate_pendiente IS NOT NULL;                    -- pantalla Aprobaciones
CREATE INDEX tickets_tenant_tipo_ix     ON core.tickets (tenant_id, tipo);
CREATE INDEX tickets_reporter_ix        ON core.tickets (reportado_por);
CREATE INDEX tickets_duplicado_ix       ON core.tickets (duplicado_de) WHERE duplicado_de IS NOT NULL;
-- Búsqueda de texto en título (ILIKE '%...%'):
CREATE INDEX tickets_titulo_trgm_ix     ON core.tickets USING gin (titulo gin_trgm_ops);
-- Kanban / dashboard (solo activos):
CREATE INDEX tickets_kanban_ix          ON core.tickets (tenant_id, etapa, creado_en DESC)
    WHERE etapa NOT IN ('produccion','cancelado','cerrado_duplicado');

-- --- Adjuntos ----------------------------------------------------------------
CREATE INDEX attachments_ticket_ix      ON core.ticket_attachments (ticket_id);
CREATE INDEX attachments_huerfanos_ix   ON core.ticket_attachments (creado_en)
    WHERE ticket_id IS NULL;                             -- purga de huérfanos >48 h
CREATE INDEX watchers_user_ix           ON core.ticket_watchers (user_id);

-- --- Specs -------------------------------------------------------------------
CREATE INDEX specs_ticket_ix            ON core.specs (ticket_id, version DESC);
CREATE INDEX spec_criteria_tenant_ix    ON core.spec_criteria (tenant_id);

-- --- Jobs y pipeline ----------------------------------------------------------
CREATE INDEX jobs_tenant_estado_ix      ON core.jobs (tenant_id, estado);
CREATE INDEX jobs_pendientes_ix         ON core.jobs (tenant_id, prioridad, creado_en)
    WHERE estado = 'pendiente_asignacion';               -- cola del planificador
CREATE INDEX job_transitions_job_ix     ON core.job_transitions (job_id, creado_en);
CREATE INDEX llm_steps_job_ix           ON core.llm_steps (job_id, creado_en);
CREATE INDEX llm_steps_ticket_etapa_ix  ON core.llm_steps (ticket_id, etapa);

-- --- Gates / QA / Security -----------------------------------------------------
CREATE INDEX gates_ticket_ix            ON core.gate_signatures (ticket_id, gate, creado_en);
CREATE INDEX gates_actor_ix             ON core.gate_signatures (actor_id, creado_en DESC);
CREATE INDEX qa_runs_ticket_ix          ON core.qa_runs (ticket_id, corrida DESC);
CREATE INDEX findings_ticket_ix         ON core.security_findings (ticket_id);
CREATE INDEX findings_bloqueantes_ix    ON core.security_findings (tenant_id, ticket_id)
    WHERE bloquea AND estado = 'abierto';                -- precondición de deploy

-- --- Consumo -------------------------------------------------------------------
CREATE INDEX usage_tokens_ticket_ix     ON core.usage_tokens (ticket_id, etapa);
CREATE INDEX usage_tokens_tenant_ix     ON core.usage_tokens (tenant_id, creado_en);
CREATE INDEX usage_loc_tenant_ciclo_ix  ON core.usage_loc (tenant_id, ciclo);
CREATE INDEX usage_loc_ticket_ix        ON core.usage_loc (ticket_id);

-- --- Integraciones ----------------------------------------------------------------
CREATE INDEX connections_tenant_ix      ON core.connections (tenant_id, categoria);
CREATE INDEX webhook_inbox_pend_ix      ON core.webhook_inbox (estado, recibido_en)
    WHERE estado IN ('pendiente','fallido');             -- cola del normalizador
CREATE INDEX webhook_inbox_tenant_ix    ON core.webhook_inbox (tenant_id, recibido_en DESC);

-- --- Notificaciones / soporte -------------------------------------------------------
CREATE INDEX notifications_user_ix      ON core.notifications (user_id, creada_en DESC);
CREATE INDEX notifications_noleidas_ix  ON core.notifications (user_id) WHERE NOT leida;
CREATE INDEX idempotency_purga_ix       ON core.idempotency_keys (creado_en);  -- TTL 48 h
CREATE INDEX escalations_abiertas_ix    ON core.escalations (tenant_id, severidad, creado_en)
    WHERE estado = 'abierta';
CREATE INDEX audit_exports_tenant_ix    ON core.audit_exports (tenant_id, creado_en DESC);

-- --- Auditoría -----------------------------------------------------------------------
CREATE INDEX audit_tenant_fecha_ix      ON audit.audit_events (tenant_id, creado_en DESC);
CREATE INDEX audit_ticket_ix            ON audit.audit_events (tenant_id, ticket_id, creado_en);
CREATE INDEX audit_actor_ix             ON audit.audit_events (tenant_id, actor_tipo, creado_en DESC);
CREATE INDEX audit_evento_ix            ON audit.audit_events (tenant_id, evento);
-- Nota: con volumen alto, particionar audit_events por rango mensual de creado_en
-- (PARTITION BY RANGE) y crear estos índices por partición.

-- --- Vectorial (pgvector) ---------------------------------------------------------------
-- HNSW con distancia coseno (BGE-M3 produce vectores normalizados).
-- m y ef_construction: valores por defecto razonables para 10^4–10^6 filas.
CREATE INDEX ticket_embeddings_hnsw_ix
    ON vec.ticket_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX doc_chunks_hnsw_ix
    ON vec.doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX definition_embeddings_hnsw_ix
    ON vec.definition_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Filtros por payload que acompañan a la búsqueda vectorial:
CREATE INDEX ticket_embeddings_tenant_ix  ON vec.ticket_embeddings (tenant_id);
CREATE INDEX doc_chunks_tenant_ix         ON vec.doc_chunks (tenant_id);
CREATE INDEX definition_emb_tenant_ix     ON vec.definition_embeddings (tenant_id, capa, modulo);

COMMIT;
