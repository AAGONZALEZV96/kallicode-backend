-- ============================================================================
-- Migración 050 — KC-DB-09-7 (3 SP): Tabla append-only de matriz de trazabilidad
-- criterio ↔ test para QA con RLS forzado y trigger de inmutabilidad.
-- ============================================================================



CREATE TABLE IF NOT EXISTS core.criteria_test_traceability (
    id                  text NOT NULL,
    tenant_id           text NOT NULL REFERENCES core.organizations(id),
    spec_id             text NOT NULL,
    criterio_id         text NOT NULL,
    qa_run_id           text NOT NULL,
    test_nodeid         text NOT NULL,
    test_suite          text NOT NULL DEFAULT 'pytest',
    resultado           text NOT NULL,
    duracion_ms         integer,
    evidencia_hash      text,
    ejecutado_en        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT ctt_resultado_chk CHECK (resultado IN ('passed', 'failed', 'skipped', 'error')),
    CONSTRAINT ctt_duracion_chk  CHECK (duracion_ms IS NULL OR duracion_ms >= 0),
    CONSTRAINT ctt_qa_run_fkey   FOREIGN KEY (tenant_id, qa_run_id)
        REFERENCES core.qa_runs(tenant_id, id) ON DELETE RESTRICT
);

-- Índice compuesto tenant-first para consultas de trazabilidad por spec/criterio y qa_run
CREATE INDEX IF NOT EXISTS idx_ctt_tenant_spec_criterio
    ON core.criteria_test_traceability (tenant_id, spec_id, criterio_id, ejecutado_en DESC);

CREATE INDEX IF NOT EXISTS idx_ctt_tenant_qa_run
    ON core.criteria_test_traceability (tenant_id, qa_run_id);

-- Trigger de inmutabilidad append-only (bloquea UPDATE y DELETE incluso para admin)
CREATE OR REPLACE FUNCTION core.criteria_traceability_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'core.criteria_test_traceability es inmutable (append-only): operación % rechazada', TG_OP
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_ctt_append_only ON core.criteria_test_traceability;
CREATE TRIGGER trg_ctt_append_only
    BEFORE UPDATE OR DELETE ON core.criteria_test_traceability
    FOR EACH ROW
    EXECUTE FUNCTION core.criteria_traceability_append_only();

-- RLS activo y forzado por tenant_id
ALTER TABLE core.criteria_test_traceability ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.criteria_test_traceability FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.criteria_test_traceability;
CREATE POLICY tenant_isolation ON core.criteria_test_traceability
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

-- Permisos: solo SELECT e INSERT para app/worker; ALL para admin (trigger protege inmutabilidad)
GRANT SELECT, INSERT ON core.criteria_test_traceability TO kallicode_app, kallicode_worker;
GRANT ALL ON core.criteria_test_traceability TO kallicode_admin;
REVOKE UPDATE, DELETE ON core.criteria_test_traceability FROM PUBLIC, kallicode_app, kallicode_worker;

