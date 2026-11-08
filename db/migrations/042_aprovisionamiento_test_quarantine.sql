-- Migración 042: Aprovisionamiento de core.test_quarantine (KC-DB-08-6)
-- Registro y aislamiento de pruebas inestables (flaky) en CI con RLS.

CREATE TABLE IF NOT EXISTS core.test_quarantine (
    id text NOT NULL,
    tenant_id text NOT NULL,
    test_node_id text NOT NULL,
    motivo text NOT NULL,
    cuarentenado_por text NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    expira_en timestamptz,
    resuelto boolean NOT NULL DEFAULT false,
    resuelto_en timestamptz,
    CONSTRAINT test_quarantine_pkey PRIMARY KEY (tenant_id, id),
    CONSTRAINT test_quarantine_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id) ON DELETE CASCADE,
    CONSTRAINT test_quarantine_motivo_chk CHECK (length(trim(motivo)) > 0)
);

CREATE INDEX IF NOT EXISTS test_quarantine_tenant_resuelto_ix 
    ON core.test_quarantine (tenant_id, resuelto, expira_en);

-- RLS y permisos
ALTER TABLE core.test_quarantine ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.test_quarantine FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.test_quarantine;
CREATE POLICY tenant_isolation ON core.test_quarantine FOR ALL USING (tenant_id = core.current_tenant());

ALTER TABLE core.test_quarantine OWNER TO kallicode_admin;
GRANT ALL ON core.test_quarantine TO kallicode_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON core.test_quarantine TO kallicode_app, kallicode_worker;