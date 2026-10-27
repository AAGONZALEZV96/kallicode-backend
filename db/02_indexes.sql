-- ============================================================================
-- KALLICODE · 02_indexes.sql — Índices de rendimiento
-- (Los índices UNIQUE que implementan reglas de negocio viven en 01_schema.sql
--  junto a su tabla; aquí van los índices de acceso.)
-- Convención (KC-DB-06-2): todo índice multi-tenant comienza por tenant_id, para que el filtro
-- implícito de RLS se resuelva dentro del índice y no después de la búsqueda. Excepciones, todas
-- documentadas con su plan de ejecución en docs/plan-indices.md (test_convencion_indices.py las
-- verifica): los índices únicos de regla de negocio (devuelven a lo sumo una fila), los barridos de
-- los workers entre tenants (no filtran por tenant), el GIN de trigramas y los HNSW vectoriales.
-- Los índices que respaldan una PRIMARY KEY o UNIQUE se justifican por su restricción.
-- ============================================================================

BEGIN;

-- --- Usuarios y sesiones ----------------------------------------------------
CREATE INDEX users_tenant_rol_ix        ON core.users (tenant_id, rol) WHERE estado = 'activo';
CREATE INDEX users_tenant_estado_ix     ON core.users (tenant_id, estado);
CREATE INDEX refresh_tokens_tenant_user_activos_ix ON core.refresh_tokens (tenant_id, user_id)
    WHERE usado_en IS NULL AND NOT revocado;
CREATE INDEX refresh_tokens_expira_ix   ON core.refresh_tokens (expira_en);   -- purga programada (excepción tenant-first: barrido entre tenants)
CREATE INDEX invitations_tenant_ix      ON core.invitations (tenant_id, estado);

-- --- Tickets: listados, filtros y búsqueda ----------------------------------
-- Listado por defecto (actualizado_en desc) con filtros:
CREATE INDEX tickets_tenant_upd_ix      ON core.tickets (tenant_id, actualizado_en DESC);
CREATE INDEX tickets_tenant_etapa_ix    ON core.tickets (tenant_id, etapa, actualizado_en DESC);
CREATE INDEX tickets_tenant_gate_ix     ON core.tickets (tenant_id, gate_pendiente)
    WHERE gate_pendiente IS NOT NULL;                    -- pantalla Aprobaciones
CREATE INDEX tickets_tenant_tipo_ix     ON core.tickets (tenant_id, tipo);
-- reportado_por y duplicado_de los cubren idx_tickets_tenant_reportado_por / _duplicado_de (migración 018).
-- Búsqueda de texto en título (ILIKE '%...%'). Excepción tenant-first: un GIN de trigramas no admite
-- tenant_id como primera columna sin btree_gin; el filtro de tenant lo aporta el btree del tablero.
CREATE INDEX tickets_titulo_trgm_ix     ON core.tickets USING gin (titulo gin_trgm_ops);
-- Kanban / dashboard (solo activos):
CREATE INDEX tickets_kanban_ix          ON core.tickets (tenant_id, etapa, creado_en DESC)
    WHERE etapa NOT IN ('produccion','cancelado','cerrado_duplicado');

-- --- Adjuntos ----------------------------------------------------------------
-- ticket_id y user_id los cubren idx_ticket_attachments_tenant_ticket_id / idx_ticket_watchers_tenant_user_id (018).
CREATE INDEX attachments_huerfanos_ix   ON core.ticket_attachments (creado_en)
    WHERE ticket_id IS NULL;                             -- purga de huérfanos >48 h (excepción tenant-first: barrido entre tenants)

-- --- Specs -------------------------------------------------------------------
CREATE INDEX specs_tenant_ticket_ix     ON core.specs (tenant_id, ticket_id, version DESC);
CREATE INDEX spec_criteria_tenant_ix    ON core.spec_criteria (tenant_id);

-- --- Jobs y pipeline ----------------------------------------------------------
CREATE INDEX jobs_tenant_estado_ix      ON core.jobs (tenant_id, estado);
CREATE INDEX jobs_pendientes_ix         ON core.jobs (tenant_id, prioridad, creado_en)
    WHERE estado = 'pendiente_asignacion';               -- cola del planificador
CREATE INDEX job_transitions_tenant_job_ix ON core.job_transitions (tenant_id, job_id, creado_en);
CREATE INDEX llm_steps_tenant_job_ix    ON core.llm_steps (tenant_id, job_id, creado_en);
CREATE INDEX llm_steps_tenant_ticket_etapa_ix ON core.llm_steps (tenant_id, ticket_id, etapa);

-- --- Gates / QA / Security -----------------------------------------------------
CREATE INDEX gates_tenant_ticket_ix     ON core.gate_signatures (tenant_id, ticket_id, gate, creado_en);
CREATE INDEX gates_tenant_actor_ix      ON core.gate_signatures (tenant_id, actor_id, creado_en DESC);
CREATE INDEX qa_runs_tenant_ticket_ix   ON core.qa_runs (tenant_id, ticket_id, corrida DESC);
-- security_findings por ticket: lo cubre idx_security_findings_tenant_ticket_id (018).
CREATE INDEX findings_bloqueantes_ix    ON core.security_findings (tenant_id, ticket_id)
    WHERE bloquea AND estado = 'abierto';                -- precondición de deploy

-- --- Consumo -------------------------------------------------------------------
CREATE INDEX usage_tokens_tenant_ticket_etapa_ix ON core.usage_tokens (tenant_id, ticket_id, etapa);
CREATE INDEX usage_tokens_tenant_ix     ON core.usage_tokens (tenant_id, creado_en);
-- KC-DB-07-2: usage_tokens y job_transitions se particionan por mes (migración 035_particion_consumo.sql);
-- sus índices pasan a ser por partición (declarados en el padre; PostgreSQL crea uno por partición).
CREATE INDEX usage_loc_tenant_ciclo_ix  ON core.usage_loc (tenant_id, ciclo);
-- usage_loc por ticket: lo cubre idx_usage_loc_tenant_ticket_id (018).

-- --- Integraciones ----------------------------------------------------------------
CREATE INDEX connections_tenant_ix      ON core.connections (tenant_id, categoria);
CREATE INDEX webhook_inbox_pend_ix      ON core.webhook_inbox (estado, recibido_en)
    WHERE estado IN ('pendiente','fallido');             -- cola del normalizador (excepción tenant-first: barrido entre tenants)
CREATE INDEX webhook_inbox_tenant_ix    ON core.webhook_inbox (tenant_id, recibido_en DESC);

-- --- Notificaciones / soporte -------------------------------------------------------
CREATE INDEX notifications_tenant_user_ix ON core.notifications (tenant_id, user_id, creada_en DESC);
-- KC-DB-07-3: webhook_inbox y notifications se particionan por mes (migración 036_particion_colas.sql); sus
-- índices pasan a ser por partición (declarados en el padre; PostgreSQL crea uno por partición).
CREATE INDEX notifications_tenant_noleidas_ix ON core.notifications (tenant_id, user_id) WHERE NOT leida;
CREATE INDEX idempotency_purga_ix       ON core.idempotency_keys (creado_en);  -- TTL 48 h (excepción tenant-first: barrido entre tenants)
CREATE INDEX escalations_abiertas_ix    ON core.escalations (tenant_id, severidad, creado_en)
    WHERE estado = 'abierta';
CREATE INDEX audit_exports_tenant_ix    ON core.audit_exports (tenant_id, creado_en DESC);

-- --- Auditoría -----------------------------------------------------------------------
CREATE INDEX audit_tenant_fecha_ix      ON audit.audit_events (tenant_id, creado_en DESC);
CREATE INDEX audit_ticket_ix            ON audit.audit_events (tenant_id, ticket_id, creado_en);
CREATE INDEX audit_actor_ix             ON audit.audit_events (tenant_id, actor_tipo, creado_en DESC);
CREATE INDEX audit_evento_ix            ON audit.audit_events (tenant_id, evento);
-- KC-DB-07-1: audit_events se particiona por rango mensual de creado_en (migración
-- 033_particion_auditoria.sql, pg_partman). Estos seis índices (los cuatro de aquí más
-- audit_events_sello_ix y audit_events_request_id_ix, de las migraciones 024 y 030) pasan a
-- ser índices POR PARTICIÓN: se declaran en la tabla padre y PostgreSQL los crea en cada
-- partición. Este script basal sigue creando la tabla simple; la migración 033 la convierte.

-- --- Vectorial (pgvector) ---------------------------------------------------------------
-- HNSW con distancia coseno (BGE-M3 produce vectores normalizados).
-- m y ef_construction: valores por defecto razonables para 10^4–10^6 filas.
-- Excepción tenant-first: un índice HNSW indexa una sola columna vectorial; el tenant se filtra con RLS.
-- Por eso la búsqueda necesita hnsw.iterative_scan (por rol, db/00_roles.sql) y un ef_search / max_scan_tuples
-- por caso de uso (por transacción: vec.configurar_busqueda, migración 038); KC-DB-08-1, docs/plan-indices.md.
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
