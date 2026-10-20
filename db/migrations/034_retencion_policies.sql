-- Migración 034: Creación de core.retention_policies (KC-DB-07-4)
-- Define ventanas de retención y ciclo de vida de datos particionados / históricos por tenant.

CREATE TABLE IF NOT EXISTS core.retention_policies (
    id TEXT NOT NULL DEFAULT ('ret_' || substr(md5(random()::text || clock_timestamp()::text), 1, 16)),
    tenant_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    retention_days INTEGER NOT NULL,
    action TEXT NOT NULL DEFAULT 'detach',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT retention_policies_pkey PRIMARY KEY (tenant_id, entity_name),
    CONSTRAINT retention_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id) ON DELETE CASCADE,
    CONSTRAINT retention_policies_days_chk CHECK (retention_days > 0),
    CONSTRAINT retention_policies_action_chk CHECK (action IN ('detach', 'drop', 'archive'))
);

CREATE INDEX IF NOT EXISTS idx_retention_policies_tenant_active
    ON core.retention_policies (tenant_id, is_active);

-- Habilitar y forzar Row-Level Security
ALTER TABLE core.retention_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.retention_policies FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.retention_policies;
CREATE POLICY tenant_isolation ON core.retention_policies
    FOR ALL
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());