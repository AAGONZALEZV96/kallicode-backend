-- kc:sin-transaccion
-- ============================================================================
-- KALLICODE · 030_audit_events_request_id.sql
-- KC-DB-05-6 — Procedencia de la acción en los eventos de auditoría (DB-F19).
--
-- 1. Índice (tenant_id, request_id) para correlacionar un evento de auditoría con
--    la traza del log central a partir del identificador de la solicitud. Es
--    parcial (solo filas con request_id): los eventos sin él no se buscan por ahí.
--    Se crea CONCURRENTLY para no bloquear las escrituras de auditoría; por eso
--    este archivo va sin transacción (cada sentencia es re-ejecutable).
-- 2. audit.eventos_sin_request_id(): cuenta, por tenant, los eventos posteriores a
--    la migración 029 (los de version_sello >= 2) que no traen request_id. Es una
--    función de consulta y NO un chequeo de core.validar_integridad(): esa batería
--    alerta con cualquier problema > 0 (apps/workers housekeeping) y, mientras la
--    API no propague el request_id, todos los eventos nuevos lo traerían vacío.
-- ============================================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_events_request_id_ix
    ON audit.audit_events (tenant_id, request_id)
    WHERE request_id IS NOT NULL;

CREATE OR REPLACE FUNCTION audit.eventos_sin_request_id(p_desde timestamptz DEFAULT NULL)
RETURNS TABLE (tenant_id text, eventos_posteriores bigint, sin_request_id bigint)
LANGUAGE sql STABLE AS $$
    SELECT e.tenant_id,
           count(*),
           count(*) FILTER (WHERE e.request_id IS NULL)
      FROM audit.audit_events e
     WHERE e.version_sello >= 2
       AND (p_desde IS NULL OR e.creado_en >= p_desde)
     GROUP BY e.tenant_id
     ORDER BY e.tenant_id
$$;

COMMENT ON FUNCTION audit.eventos_sin_request_id(timestamptz) IS
'KC-DB-05-6: por tenant, eventos posteriores a la migración 029 (version_sello >= 2) y cuántos no traen request_id. Con RLS, un tenant solo ve el suyo.';
