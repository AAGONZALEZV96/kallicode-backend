-- ============================================================================
-- KALLICODE · 03_triggers.sql — Funciones y triggers
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- T1 · actualizado_en automático
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.actualizado_en := now();
    RETURN NEW;
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'organizations','users','production_lines','tickets','jobs',
        'security_findings','connections','model_providers','branch_map'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated BEFORE UPDATE ON core.%I
             FOR EACH ROW EXECUTE FUNCTION core.set_updated_at()', t, t);
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- T2 · Correlativo visible KC-#### por tenant
--      Serializado con UPDATE ... RETURNING sobre ticket_counters (bloqueo de fila):
--      dos inserciones concurrentes del mismo tenant jamás repiten número.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.assign_ticket_numero() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    IF NEW.numero IS NOT NULL THEN
        RETURN NEW;  -- permitir carga histórica con número explícito
    END IF;
    INSERT INTO core.ticket_counters (tenant_id) VALUES (NEW.tenant_id)
        ON CONFLICT (tenant_id) DO NOTHING;
    UPDATE core.ticket_counters
       SET ultimo = ultimo + 1
     WHERE tenant_id = NEW.tenant_id
     RETURNING ultimo INTO n;
    NEW.numero := 'KC-' || n::text;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_tickets_numero
    BEFORE INSERT ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.assign_ticket_numero();

-- ----------------------------------------------------------------------------
-- T3 · Cadena de hashes de auditoría
--      sello = SHA-256(sello_previo || tenant || evento || resumen || datos || fecha)
--      chain_heads serializa por tenant (UPDATE bloquea la fila): la cadena
--      nunca se bifurca. La API NO calcula sellos: solo inserta el evento.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.seal_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE prev text;
BEGIN
    INSERT INTO audit.chain_heads (tenant_id) VALUES (NEW.tenant_id)
        ON CONFLICT (tenant_id) DO NOTHING;
    -- Bloquea la cabeza de cadena del tenant hasta el COMMIT:
    UPDATE audit.chain_heads
       SET eventos = eventos + 1
     WHERE tenant_id = NEW.tenant_id
     RETURNING ultimo_sello INTO prev;

    NEW.sello_previo := prev;
    NEW.creado_en    := now();
    NEW.sello := encode(sha256(convert_to(
        prev
        || NEW.tenant_id
        || NEW.evento
        || NEW.resumen
        || coalesce(NEW.datos::text, '')
        || coalesce(NEW.ticket_id, '')
        || coalesce(NEW.actor_id, '')
        || to_char(NEW.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'UTF8')), 'hex');

    UPDATE audit.chain_heads
       SET ultimo_sello = NEW.sello
     WHERE tenant_id = NEW.tenant_id;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_audit_seal
    BEFORE INSERT ON audit.audit_events
    FOR EACH ROW EXECUTE FUNCTION audit.seal_event();

-- Inmutabilidad: la tabla es append-only también a nivel de motor.
CREATE OR REPLACE FUNCTION audit.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.audit_events es append-only: % prohibido', TG_OP
        USING ERRCODE = 'raise_exception';
END $$;

CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON audit.audit_events
    FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation();

-- Función de verificación de integridad de la cadena (usada por §validación
-- y por el endpoint GET /audit/integrity).
CREATE OR REPLACE FUNCTION audit.verify_chain(p_tenant text,
                                              p_desde timestamptz DEFAULT NULL,
                                              p_hasta timestamptz DEFAULT NULL)
RETURNS TABLE (integra boolean, eventos_verificados bigint, primer_evento_roto bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r record;
    esperado text;
    n bigint := 0;
BEGIN
    integra := true; primer_evento_roto := NULL;
    FOR r IN
        SELECT * FROM audit.audit_events
         WHERE tenant_id = p_tenant
           AND (p_desde IS NULL OR creado_en >= p_desde)
           AND (p_hasta IS NULL OR creado_en <= p_hasta)
         ORDER BY id
    LOOP
        n := n + 1;
        esperado := encode(sha256(convert_to(
            r.sello_previo || r.tenant_id || r.evento || r.resumen
            || coalesce(r.datos::text,'') || coalesce(r.ticket_id,'')
            || coalesce(r.actor_id,'')
            || to_char(r.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'UTF8')), 'hex');
        IF esperado <> r.sello THEN
            integra := false;
            primer_evento_roto := r.id;
            EXIT;
        END IF;
    END LOOP;
    eventos_verificados := n;
    RETURN NEXT;
END $$;

-- ----------------------------------------------------------------------------
-- T4 · Inmutabilidad de specs: una versión emitida solo puede cambiar de estado
--      (pendiente_gate1 → aprobada | reemplazada | rechazada); el contenido no.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.spec_freeze() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.spec_md_ref    IS DISTINCT FROM OLD.spec_md_ref
    OR NEW.scope_paths    IS DISTINCT FROM OLD.scope_paths
    OR NEW.version        IS DISTINCT FROM OLD.version
    OR NEW.ticket_id      IS DISTINCT FROM OLD.ticket_id
    OR NEW.plan_tests     IS DISTINCT FROM OLD.plan_tests
    OR NEW.migraciones    IS DISTINCT FROM OLD.migraciones THEN
        RAISE EXCEPTION 'Las versiones de spec son inmutables: solo puede cambiar el estado';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_specs_freeze
    BEFORE UPDATE ON core.specs
    FOR EACH ROW EXECUTE FUNCTION core.spec_freeze();

-- ----------------------------------------------------------------------------
-- T5 · Precondición del Gate 3 (constraint trigger, DEFERRABLE):
--      no puede firmarse sin la aprobación previa de los gates 1 y 2.
--      (La app también lo valida; esto es el cinturón del motor.)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.gate3_requires_gates12() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.gate = 3 AND NEW.accion = 'aprobado' THEN
        IF (SELECT count(DISTINCT gate) FROM core.gate_signatures
             WHERE ticket_id = NEW.ticket_id
               AND gate IN (1,2) AND accion = 'aprobado') < 2 THEN
            RAISE EXCEPTION 'Gate 3 requiere los gates 1 y 2 aprobados (ticket %)', NEW.ticket_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE CONSTRAINT TRIGGER trg_gate3_precondicion
    AFTER INSERT ON core.gate_signatures
    DEFERRABLE INITIALLY IMMEDIATE
    FOR EACH ROW EXECUTE FUNCTION core.gate3_requires_gates12();

-- ----------------------------------------------------------------------------
-- T6 · Agregado de consumo LOC por ciclo (QU-1)
--      Mantiene usage_cycles y calcula el umbral con el límite del plan vigente.
--      La app lee usage_cycles.umbral; el trigger nunca notifica (eso es de la app).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.accumulate_loc() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    lim integer;
    consumidas integer;
    nuevo_umbral text;
BEGIN
    SELECT p.loc_mes INTO lim
      FROM core.subscriptions s JOIN core.plans p ON p.codigo = s.plan_codigo
     WHERE s.tenant_id = NEW.tenant_id AND s.finaliza_el IS NULL;

    INSERT INTO core.usage_cycles (tenant_id, ciclo, loc_consumidas)
    VALUES (NEW.tenant_id, NEW.ciclo, greatest(NEW.netas, 0))
    ON CONFLICT (tenant_id, ciclo) DO UPDATE
       SET loc_consumidas = core.usage_cycles.loc_consumidas + greatest(NEW.netas, 0),
           actualizado_en = now()
    RETURNING loc_consumidas INTO consumidas;

    IF lim IS NULL THEN
        nuevo_umbral := 'normal';                        -- Enterprise: sin límite
    ELSIF consumidas >= lim THEN
        nuevo_umbral := 'agotado';
    ELSIF consumidas >= lim * 0.8 THEN
        nuevo_umbral := 'aviso_80';
    ELSE
        nuevo_umbral := 'normal';
    END IF;

    UPDATE core.usage_cycles
       SET umbral = nuevo_umbral
     WHERE tenant_id = NEW.tenant_id AND ciclo = NEW.ciclo
       AND umbral IS DISTINCT FROM nuevo_umbral;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_usage_loc_acumula
    AFTER INSERT ON core.usage_loc
    FOR EACH ROW EXECUTE FUNCTION core.accumulate_loc();

-- ----------------------------------------------------------------------------
-- T7 · Sincronía jobs ⇄ production_lines
--      Al asignar/liberar línea en jobs, la fila de production_lines se
--      actualiza en la misma transacción (una sola fuente de verdad visible).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.sync_line_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Liberación (job terminó, se pausó sin línea o cambió de línea):
    IF TG_OP = 'UPDATE'
       AND OLD.linea IS NOT NULL
       AND (NEW.linea IS DISTINCT FROM OLD.linea
            OR NEW.estado IN ('produccion','cancelado','cerrado_duplicado')) THEN
        UPDATE core.production_lines
           SET estado = 'disponible', job_id = NULL
         WHERE tenant_id = OLD.tenant_id AND numero = OLD.linea AND job_id = OLD.id;
    END IF;
    -- Ocupación:
    IF NEW.linea IS NOT NULL
       AND NEW.estado NOT IN ('produccion','cancelado','cerrado_duplicado') THEN
        UPDATE core.production_lines
           SET estado = 'ocupada', job_id = NEW.id
         WHERE tenant_id = NEW.tenant_id AND numero = NEW.linea;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_jobs_sync_linea
    AFTER INSERT OR UPDATE OF linea, estado ON core.jobs
    FOR EACH ROW EXECUTE FUNCTION core.sync_line_state();

-- ----------------------------------------------------------------------------
-- T8 · Historial de transiciones de job automático
--      Cada cambio de estado del job queda en job_transitions aunque la app
--      olvide registrarlo (la app añade motivo/contexto en su propio insert).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.log_job_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.estado IS DISTINCT FROM OLD.estado THEN
        INSERT INTO core.job_transitions (tenant_id, job_id, de_estado, a_estado, actor)
        VALUES (NEW.tenant_id, NEW.id, OLD.estado, NEW.estado,
                coalesce(current_setting('app.actor', true), 'sistema'));
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_jobs_transicion
    AFTER UPDATE OF estado ON core.jobs
    FOR EACH ROW EXECUTE FUNCTION core.log_job_transition();

-- ----------------------------------------------------------------------------
-- T9 · Espejo ticket ⇄ job: la etapa visible del ticket sigue al estado del job.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.mirror_ticket_stage() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_etapa text;
    v_gate smallint;
BEGIN
    v_gate := NULL;
    CASE NEW.estado
        WHEN 'gate1' THEN v_etapa := 'design';   v_gate := 1;
        WHEN 'gate2' THEN v_etapa := 'qa';       v_gate := 2;
        WHEN 'gate3' THEN v_etapa := 'deploy';   v_gate := 3;
        WHEN 'deploy_preflight' THEN v_etapa := 'deploy';
        WHEN 'pendiente_asignacion' THEN v_etapa := 'triage';
        WHEN 'pausado_por_cuota' THEN v_etapa := 'en_cola_por_cuota';
        WHEN 'escalado_humano' THEN v_etapa := NULL;  -- conserva la etapa visible
        ELSE v_etapa := NEW.estado;
    END CASE;

    IF v_etapa IS NOT NULL THEN
        UPDATE core.tickets
           SET etapa = v_etapa,
               gate_pendiente = v_gate,
               cerrado_en = CASE WHEN v_etapa IN ('produccion','cancelado','cerrado_duplicado')
                                 THEN coalesce(cerrado_en, now()) ELSE cerrado_en END
         WHERE id = NEW.ticket_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_jobs_espejo_ticket
    AFTER INSERT OR UPDATE OF estado ON core.jobs
    FOR EACH ROW EXECUTE FUNCTION core.mirror_ticket_stage();

-- ----------------------------------------------------------------------------
-- T10 · El creador del ticket siempre es visualizador (y no puede eliminarse).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.creator_is_watcher() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.reportado_por IS NOT NULL THEN
        INSERT INTO core.ticket_watchers (tenant_id, ticket_id, user_id)
        VALUES (NEW.tenant_id, NEW.id, NEW.reportado_por)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_tickets_watcher_creador
    AFTER INSERT ON core.tickets
    FOR EACH ROW EXECUTE FUNCTION core.creator_is_watcher();

CREATE OR REPLACE FUNCTION core.protect_creator_watcher() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE creador text;
BEGIN
    SELECT reportado_por INTO creador FROM core.tickets WHERE id = OLD.ticket_id;
    IF creador = OLD.user_id THEN
        RAISE EXCEPTION 'El creador del ticket no puede eliminarse como visualizador';
    END IF;
    RETURN OLD;
END $$;

CREATE TRIGGER trg_watchers_protege_creador
    BEFORE DELETE ON core.ticket_watchers
    FOR EACH ROW EXECUTE FUNCTION core.protect_creator_watcher();

COMMIT;
