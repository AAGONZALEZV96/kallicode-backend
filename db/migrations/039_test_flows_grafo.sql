-- ============================================================================
-- KALLICODE · 039_test_flows_grafo.sql
-- KC-QA-B3-12 — Registro de Flujos de Prueba y Coberturas en el Grafo
-- (cm.test_flows, cm.test_flow_covers) para regresión dirigida por impacto.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE TABLE IF NOT EXISTS cm.test_flows (
    id             text NOT NULL,                   -- {criterion_id}:{kind}
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    spec_id        text NOT NULL,
    criterion_id   text NOT NULL,
    kind           text NOT NULL,
    path           text NOT NULL,
    creado_en      timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT test_flows_kind_chk CHECK (kind IN ('unit', 'api', 'e2e'))
);

CREATE TABLE IF NOT EXISTS cm.test_flow_covers (
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    test_flow_id   text NOT NULL,
    module_path    text NOT NULL,
    creado_en      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, test_flow_id, module_path),
    FOREIGN KEY (tenant_id, test_flow_id) REFERENCES cm.test_flows(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_test_flows_spec ON cm.test_flows (tenant_id, spec_id);
CREATE INDEX IF NOT EXISTS idx_test_flow_covers_module ON cm.test_flow_covers (tenant_id, module_path);

GRANT USAGE ON SCHEMA cm TO kallicode_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON cm.test_flows TO kallicode_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON cm.test_flow_covers TO kallicode_app;

ALTER TABLE cm.test_flows ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.test_flows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm.test_flows;
CREATE POLICY tenant_isolation ON cm.test_flows
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

ALTER TABLE cm.test_flow_covers ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.test_flow_covers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm.test_flow_covers;
CREATE POLICY tenant_isolation ON cm.test_flow_covers
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
