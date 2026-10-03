-- ============================================================================
-- KALLICODE · 012_unicidad_tenant_id.sql
-- KC-DB-03-1 (DB-F01) — UNIQUE (tenant_id, id) en las tablas padre.
--
-- Paso previo de KC-DB-03-2: una FK compuesta (tenant_id, ticket_id) ->
-- tickets(tenant_id, id) exige que esa pareja sea única en el padre. Como id
-- ya es PK, (tenant_id, id) es único siempre: esta migración no puede fallar
-- por datos duplicados.
--
-- Tablas: las 7 que reciben las 41 FK escalares que vigila V21 (011):
--   tickets (16) · users (14) · jobs (4) · specs (4) · qa_runs (1) ·
--   connections (1) · onboarding_documents (1)
-- core.organizations NO entra (el plan la listaba): no tiene tenant_id porque
-- ES el tenant; sus FK (tenant_id -> organizations.id) ya son coherentes.
--
-- Sin bloquear escrituras: CREATE UNIQUE INDEX CONCURRENTLY (no admite
-- transacción) y después ADD CONSTRAINT ... USING INDEX (instantáneo, solo
-- catálogo). Reejecutable: si falla a mitad, se vuelve a lanzar el runner.
-- Si un CONCURRENTLY se interrumpe deja un índice INVÁLIDO y el ADD CONSTRAINT
-- fallará con un error explícito: borrarlo con
--   DROP INDEX CONCURRENTLY core.<tabla>_tenant_id_uq;
-- y volver a ejecutar el runner.
-- ============================================================================
-- kc:sin-transaccion

-- Si una tabla está ocupada, abortar en 5 s en lugar de encolar a los demás.
SET lock_timeout = '5s';
-- Tope por sentencia: un índice CONCURRENTLY sobre una tabla grande puede
-- tardar minutos; 30 min es una red de seguridad, no el tiempo esperado.
SET statement_timeout = '30min';

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS tickets_tenant_id_uq
    ON core.tickets (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS users_tenant_id_uq
    ON core.users (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS jobs_tenant_id_uq
    ON core.jobs (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS specs_tenant_id_uq
    ON core.specs (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS qa_runs_tenant_id_uq
    ON core.qa_runs (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS connections_tenant_id_uq
    ON core.connections (tenant_id, id);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS onboarding_documents_tenant_id_uq
    ON core.onboarding_documents (tenant_id, id);

-- Convertir cada índice en restricción (solo si aún no lo es).
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['tickets', 'users', 'jobs', 'specs', 'qa_runs',
                             'connections', 'onboarding_documents']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conrelid = format('core.%I', t)::regclass
                          AND conname = t || '_tenant_id_uq') THEN
            EXECUTE format('ALTER TABLE core.%I ADD CONSTRAINT %I UNIQUE USING INDEX %I',
                           t, t || '_tenant_id_uq', t || '_tenant_id_uq');
        END IF;
    END LOOP;
END $$;

RESET lock_timeout;
RESET statement_timeout;
