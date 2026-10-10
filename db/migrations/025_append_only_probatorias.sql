-- ============================================================================
-- KALLICODE · 025_append_only_probatorias.sql
-- KC-DB-05-2 — Append-only y congelación de las tablas probatorias.
--
-- 04_rls.sql:15 concede SELECT, INSERT, UPDATE y DELETE sobre las 36 tablas de
-- core al rol de la aplicación. La única congelación de contenido era
-- trg_specs_freeze (core.specs) y la única tabla append-only, audit.audit_events:
-- quedaban reescribibles y borrables la firma humana que autoriza el despliegue,
-- el registro de los pasos del LLM, las transiciones de los jobs, las corridas y
-- resultados de QA, los hallazgos de seguridad y el consumo de LOC.
--
--   * gate_signatures, llm_steps, job_transitions, qa_runs, qa_results y
--     usage_loc pasan a ser append-only: kallicode_app pierde UPDATE y DELETE y
--     un trigger lo impide también a nivel de motor (incluido un rol privilegiado).
--   * security_findings admite únicamente la transición de estado y de veredicto,
--     con justificación obligatoria; rechaza cambiar regla, archivo, línea,
--     severidad o título (y cualquier otro campo) y no se puede borrar.
--   * Una corrección se registra como fila de compensación: usage_loc con
--     tipo = 'ajuste' y una corrida de QA nueva (docs/append_only_probatorias.md).
--
-- Cascadas referenciales: borrar un ticket o un job sigue arrastrando a sus hijos
-- (ON DELETE CASCADE) y usage_loc.ticket_id sigue pasando a NULL (ON DELETE SET
-- NULL). Esas acciones las dispara el padre y se ejecutan a profundidad de trigger
-- mayor que 1; el trigger solo bloquea la mutación directa (profundidad 1). La
-- aplicación no borra tickets ni jobs (usa eliminado_en).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Permisos: la aplicación pierde la mutación.
-- ---------------------------------------------------------------------------
REVOKE UPDATE, DELETE ON
    core.gate_signatures, core.llm_steps, core.job_transitions,
    core.qa_runs, core.qa_results, core.usage_loc,
    core.security_findings
FROM kallicode_app;

-- security_findings: solo puede cambiar lo que una transición legítima toca.
GRANT UPDATE (estado, veredicto, justificacion, actualizado_en)
    ON core.security_findings TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 2. Seis tablas append-only: ninguna mutación directa, tampoco para un rol
--    privilegiado (que tendría que desactivar el trigger a propósito).
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.probatoria_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Las acciones referenciales (CASCADE / SET NULL) las dispara el borrado del
    -- padre: su trigger de sistema es el nivel 1 y esta fila se mueve en el nivel 2.
    IF pg_trigger_depth() > 1 THEN
        RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    RAISE EXCEPTION '%.% es append-only: % prohibido; corrige con una fila de compensacion',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP USING ERRCODE = 'raise_exception';
END $$;

CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.gate_signatures
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();
CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.llm_steps
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();
CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.job_transitions
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();
CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.qa_runs
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();
CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.qa_results
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();
CREATE TRIGGER trg_probatoria_append_only
    BEFORE UPDATE OR DELETE ON core.usage_loc
    FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();

-- ---------------------------------------------------------------------------
-- 3. security_findings: solo la transición de estado y de veredicto.
--    Inmutables: id, tenant_id, ticket_id, origen, regla, archivo, linea,
--    severidad, titulo, fix_diff_ref, bloquea y creado_en. Cambiar estado o
--    veredicto exige una justificación no vacía y distinta de la anterior.
--    (trg_security_findings_updated sigue fijando actualizado_en.)
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.security_findings_transicion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1 THEN
            RETURN OLD;                               -- cascada desde el ticket
        END IF;
        RAISE EXCEPTION 'core.security_findings es append-only: DELETE prohibido'
            USING ERRCODE = 'raise_exception';
    END IF;

    IF NEW.id            IS DISTINCT FROM OLD.id
    OR NEW.tenant_id     IS DISTINCT FROM OLD.tenant_id
    OR NEW.ticket_id     IS DISTINCT FROM OLD.ticket_id
    OR NEW.origen        IS DISTINCT FROM OLD.origen
    OR NEW.regla         IS DISTINCT FROM OLD.regla
    OR NEW.archivo       IS DISTINCT FROM OLD.archivo
    OR NEW.linea         IS DISTINCT FROM OLD.linea
    OR NEW.severidad     IS DISTINCT FROM OLD.severidad
    OR NEW.titulo        IS DISTINCT FROM OLD.titulo
    OR NEW.fix_diff_ref  IS DISTINCT FROM OLD.fix_diff_ref
    OR NEW.bloquea       IS DISTINCT FROM OLD.bloquea
    OR NEW.creado_en     IS DISTINCT FROM OLD.creado_en THEN
        RAISE EXCEPTION
            'core.security_findings: solo se admite la transicion de estado y de veredicto'
            USING ERRCODE = 'raise_exception';
    END IF;

    IF NEW.estado IS DISTINCT FROM OLD.estado OR NEW.veredicto IS DISTINCT FROM OLD.veredicto THEN
        IF NEW.justificacion IS NULL OR btrim(NEW.justificacion) = '' THEN
            RAISE EXCEPTION
                'core.security_findings: la transicion de estado o veredicto exige justificacion'
                USING ERRCODE = 'raise_exception';
        END IF;
        IF OLD.justificacion IS NOT NULL AND NEW.justificacion = OLD.justificacion THEN
            RAISE EXCEPTION
                'core.security_findings: la justificacion debe explicar esta transicion, no repetir la anterior'
                USING ERRCODE = 'raise_exception';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- Se llama «a_» para ejecutarse antes que trg_security_findings_updated (orden alfabético).
CREATE TRIGGER trg_a_security_findings_transicion
    BEFORE UPDATE OR DELETE ON core.security_findings
    FOR EACH ROW EXECUTE FUNCTION core.security_findings_transicion();

COMMENT ON FUNCTION core.probatoria_append_only() IS
'KC-DB-05-2: tablas probatorias append-only (gate_signatures, llm_steps, job_transitions, qa_runs, qa_results, usage_loc). Bloquea UPDATE/DELETE directos; deja pasar las acciones referenciales del padre.';
COMMENT ON FUNCTION core.security_findings_transicion() IS
'KC-DB-05-2: security_findings solo admite la transicion de estado y de veredicto con justificacion; no se puede borrar directamente.';
