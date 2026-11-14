-- ============================================================================
-- Migración 048 — KC-DB-09-4 (5 SP): Borrado lógico unificado (deleted_at)
-- y procedimiento de supresión física por tenant (core.suprimir_tenant).
-- ============================================================================


-- 1. Estandarizar deleted_at en core.organizations y core.onboarding_documents
--    (sincronizado con eliminado_en existente para no romper código legado)
ALTER TABLE core.organizations
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
UPDATE core.organizations
   SET deleted_at = eliminado_en
 WHERE deleted_at IS NULL AND eliminado_en IS NOT NULL;

ALTER TABLE core.onboarding_documents
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
UPDATE core.onboarding_documents
   SET deleted_at = eliminado_en
 WHERE deleted_at IS NULL AND eliminado_en IS NOT NULL;

-- Estandarizar deleted_at en las demás entidades principales del dominio
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'core.users',
        'core.connections',
        'core.tickets',
        'core.specs',
        'core.jobs',
        'core.production_lines',
        'core.subscriptions',
        'core.invitations',
        'core.ticket_attachments'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS deleted_at timestamptz;', t);
    END LOOP;
END $$;

-- 2. Tabla de actas anónimas verificables de supresión física (derecho al olvido)
CREATE TABLE IF NOT EXISTS audit.tenant_deletion_receipts (
    id                  text PRIMARY KEY,
    tenant_hash         text NOT NULL,
    solicitado_por      text NOT NULL,
    motivo              text NOT NULL DEFAULT 'derecho_al_olvido',
    filas_eliminadas    jsonb NOT NULL DEFAULT '{}'::jsonb,
    sello_acta          text NOT NULL,
    ejecutado_en        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tdr_tenant_hash_chk CHECK (tenant_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT tdr_sello_chk       CHECK (sello_acta ~ '^[0-9a-f]{64}$')
);

GRANT SELECT ON audit.tenant_deletion_receipts TO kallicode_admin;
REVOKE UPDATE, DELETE ON audit.tenant_deletion_receipts FROM PUBLIC, kallicode_app, kallicode_worker, kallicode_admin;

-- 3. Procedimiento core.suprimir_tenant(p_tenant_id, p_solicitado_por, p_motivo)
CREATE OR REPLACE FUNCTION core.suprimir_tenant(
    p_tenant_id      text,
    p_solicitado_por text DEFAULT 'sistema',
    p_motivo         text DEFAULT 'derecho_al_olvido'
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, core, cm, vec, audit
AS $$
DECLARE
    v_existe        boolean;
    v_conteos       jsonb := '{}'::jsonb;
    v_rows          bigint;
    v_acta_id       text;
    v_tenant_hash   text;
    v_sello         text;
    v_tabla         text;
    v_tablas_orden  text[] := ARRAY[
        -- Esquema vec (hojas vectoriales)
        'vec.incident_embeddings',
        'vec.ticket_embeddings',
        'vec.definition_embeddings',
        'vec.doc_chunks',
        -- Esquema cm (dependientes -> raíz)
        'cm.incident_nodes',
        'cm.incidents',
        'cm.test_flow_covers',
        'cm.test_flows',
        'cm.doc_chunk_links',
        'cm.curation_queue',
        'cm.nuggets',
        'cm.edges',
        'cm.nodes',
        'cm.db_columns',
        'cm.db_tables',
        'cm.symbols',
        'cm.definitions',
        'cm.concepts',
        'cm.files',
        'cm.snapshot_vigente',
        'cm.repo_snapshots',
        -- Esquema core (dependientes -> raíz)
        'core.criteria_test_traceability',
        'core.qa_results',
        'core.qa_runs',
        'core.spec_criteria',
        'core.spec_alternatives',
        'core.specs',
        'core.gate_signatures',
        'core.security_findings',
        'core.escalations',
        'core.job_transitions',
        'core.llm_steps',
        'core.production_lines',
        'core.jobs',
        'core.ticket_attachments',
        'core.ticket_watchers',
        'core.audit_exports',
        'core.notifications',
        'core.webhook_inbox',
        'core.usage_loc',
        'core.usage_tokens',
        'core.usage_cycles',
        'core.tickets',
        'core.ticket_counters',
        'core.onboarding_documents',
        'core.upgrade_requests',
        'core.subscriptions',
        'core.model_assignments',
        'core.model_providers',
        'core.branch_map',
        'core.connections',
        'core.idempotency_keys',
        'core.password_reset_tokens',
        'core.refresh_tokens',
        'core.invitations',
        'core.users'
    ];
    v_tablas_append_only text[] := ARRAY[
        'audit.audit_events',
        'core.criteria_test_traceability',
        'core.gate_signatures',
        'core.llm_steps',
        'core.job_transitions',
        'core.qa_runs',
        'core.qa_results',
        'core.usage_loc',
        'core.security_findings',
        'core.ticket_watchers'
    ];
BEGIN
    IF p_tenant_id IS NULL OR length(trim(p_tenant_id)) = 0 THEN
        RAISE EXCEPTION 'tenant_id obligatorio para supresión física';
    END IF;

    SELECT EXISTS (SELECT 1 FROM core.organizations WHERE id = p_tenant_id)
      INTO v_existe;

    IF NOT v_existe THEN
        RAISE EXCEPTION 'Organización % no existe o ya fue suprimida', p_tenant_id;
    END IF;

    -- Desactivar temporalmente triggers append-only/protección durante la purga
    FOREACH v_tabla IN ARRAY v_tablas_append_only
    LOOP
        IF to_regclass(v_tabla) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %s DISABLE TRIGGER USER', v_tabla);
        END IF;
    END LOOP;

    FOREACH v_tabla IN ARRAY v_tablas_orden
    LOOP
        IF to_regclass(v_tabla) IS NOT NULL THEN
            EXECUTE format('DELETE FROM %s WHERE tenant_id = $1', v_tabla)
              USING p_tenant_id;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            IF v_rows > 0 THEN
                v_conteos := v_conteos || jsonb_build_object(v_tabla, v_rows);
            END IF;
        END IF;
    END LOOP;

    -- Eliminar trazas de auditoría nominativas
    IF to_regclass('audit.audit_events') IS NOT NULL THEN
        DELETE FROM audit.audit_events WHERE tenant_id = p_tenant_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows > 0 THEN
            v_conteos := v_conteos || jsonb_build_object('audit.audit_events', v_rows);
        END IF;
    END IF;

    IF to_regclass('audit.chain_heads') IS NOT NULL THEN
        DELETE FROM audit.chain_heads WHERE tenant_id = p_tenant_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows > 0 THEN
            v_conteos := v_conteos || jsonb_build_object('audit.chain_heads', v_rows);
        END IF;
    END IF;

    -- Eliminar la raíz core.organizations
    DELETE FROM core.organizations WHERE id = p_tenant_id;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    v_conteos := v_conteos || jsonb_build_object('core.organizations', v_rows);

    -- Reactivar triggers
    FOREACH v_tabla IN ARRAY v_tablas_append_only
    LOOP
        IF to_regclass(v_tabla) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %s ENABLE TRIGGER USER', v_tabla);
        END IF;
    END LOOP;

    -- Emitir acta anónima verificable usando sha256 nativo de Postgres
    v_tenant_hash := encode(sha256(p_tenant_id::bytea), 'hex');
    v_acta_id     := 'del_' || substr(encode(sha256((p_tenant_id || clock_timestamp()::text)::bytea), 'hex'), 1, 24);
    v_sello       := encode(sha256((v_acta_id || '|' || v_tenant_hash || '|' || v_conteos::text)::bytea), 'hex');

    INSERT INTO audit.tenant_deletion_receipts (
        id, tenant_hash, solicitado_por, motivo, filas_eliminadas, sello_acta
    )
    VALUES (
        v_acta_id, v_tenant_hash, COALESCE(NULLIF(trim(p_solicitado_por), ''), 'sistema'),
        COALESCE(NULLIF(trim(p_motivo), ''), 'derecho_al_olvido'), v_conteos, v_sello
    );

    RETURN v_acta_id;
END;
$$;

REVOKE ALL ON FUNCTION core.suprimir_tenant(text, text, text) FROM PUBLIC, kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION core.suprimir_tenant(text, text, text) TO kallicode_admin;
