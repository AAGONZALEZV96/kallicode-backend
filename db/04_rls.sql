-- ============================================================================
-- KALLICODE · 04_rls.sql — Row-Level Security y permisos
-- La API ejecuta al abrir cada transacción:
--     SET LOCAL app.tenant_id = '<org del JWT>';
-- kallicode_app queda confinado al tenant de la sesión; un recurso ajeno
-- es indistinguible de uno inexistente.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Permisos base
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA core, vec, audit TO kallicode_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO kallicode_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA vec  TO kallicode_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO kallicode_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA audit TO kallicode_app;

-- Auditoría: SOLO INSERT y SELECT (append-only también por permisos).
GRANT SELECT, INSERT ON audit.audit_events TO kallicode_app;
GRANT SELECT, INSERT, UPDATE ON audit.chain_heads TO kallicode_app; -- la actualiza el trigger
REVOKE UPDATE, DELETE, TRUNCATE ON audit.audit_events FROM kallicode_app;

-- Catálogos globales: solo lectura para la app.
REVOKE INSERT, UPDATE, DELETE ON core.plans FROM kallicode_app;

-- ----------------------------------------------------------------------------
-- RLS: política uniforme por tenant_id
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT schemaname, tablename
          FROM pg_tables
         WHERE schemaname IN ('core','vec','audit')
           AND tablename NOT IN ('plans')                    -- global sin RLS
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',  r.schemaname, r.tablename);
        IF r.tablename = 'organizations' THEN
            -- La tabla raíz filtra por su propio id:
            EXECUTE format($sql$
                CREATE POLICY tenant_isolation ON %I.%I
                USING (id = core.current_tenant())
                WITH CHECK (id = core.current_tenant())
            $sql$, r.schemaname, r.tablename);
        ELSE
            EXECUTE format($sql$
                CREATE POLICY tenant_isolation ON %I.%I
                USING (tenant_id = core.current_tenant())
                WITH CHECK (tenant_id = core.current_tenant())
            $sql$, r.schemaname, r.tablename);
        END IF;
    END LOOP;
END $$;

-- idempotency_keys no tiene FK a organizations pero sí tenant_id: cubierta arriba.
-- ticket_counters: cubierta (PK = tenant_id → columna tenant_id existe).

-- ----------------------------------------------------------------------------
-- Rol administrador (migraciones, soporte): sin RLS.
-- El atributo BYPASSRLS se concede al usuario de login que hereda este rol:
--     ALTER ROLE <usuario_migraciones> BYPASSRLS;
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA core, vec, audit TO kallicode_admin;
GRANT ALL ON ALL TABLES IN SCHEMA core, vec TO kallicode_admin;
GRANT SELECT, INSERT ON audit.audit_events TO kallicode_admin;
GRANT ALL ON audit.chain_heads TO kallicode_admin;

-- Defaults para objetos futuros:
ALTER DEFAULT PRIVILEGES IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kallicode_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA vec
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kallicode_app;

COMMIT;
