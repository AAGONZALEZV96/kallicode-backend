-- ============================================================================
-- KALLICODE · 055_realimentacion_tickets.sql
-- KC-CM-10-4 — Realimentación del grafo desde los tickets entregados.
--
-- Al entregarse un ticket (etapa 'produccion') CodeMapping analiza su expediente con el LLM y
-- propone candidatos a nugget y a incidente. Nada entra al grafo sin curación humana. Esta
-- migración deja que la base lo garantice:
--
--   1. cm.nuggets con origen 'ticket'.
--        origen     'documento' (por defecto: los de KC-CM-08-2) | 'ticket'.
--        ticket_id  ticket del que sale el nugget (FK compuesta a core.tickets, en cascada: la
--                   evidencia de un nugget debe poder resolverse, igual que su chunk).
--        evidencia_ref pasa a admitir NULL: solo los nugget de documento citan un chunk de
--                   vec.doc_chunks (la FK compuesta de 041 no se evalúa con NULL, MATCH SIMPLE).
--                   El nugget de ticket guarda en `seccion` el elemento del expediente
--                   (p. ej. 'qa:AC-2') y en `cita` el fragmento literal que lo respalda.
--        Las reglas de 041 no cambian: nace 'propuesto', abre su revisión pendiente en
--        cm.curation_queue (el payload incluye ahora origen y ticket_id) y solo cambia de estado
--        con una resolución humana registrada.
--   2. Candidatos a incidente en cm.curation_queue (tipo 'incidente'). cm.incidents NO tiene
--        estado de curación y {{incidents}} lo lee directamente, así que el candidato no se
--        escribe ahí: queda en la cola con el registro completo en el payload y como mucho uno
--        por ticket (índice único parcial). cm.resolver_incidente() registra la decisión y, si
--        se aprueba, materializa el incidente (upsert por ticket) y sus enlaces a nodos.
--   3. cm.realimentacion_tickets: cerrojo de idempotencia por ticket. Un ticket ya realimentado
--        ('completado') no se vuelve a enviar al LLM aunque el cierre se reprocese, así que una
--        redacción distinta del modelo no puede duplicar candidatos. RLS activa y forzada.
--   4. core.usage_tokens admite origen 'codemapping_feedback' (el consumo del paso, con el
--        ticket del que sale).
--
-- No es destructiva: ADD COLUMN, DROP NOT NULL, ADD CONSTRAINT, CREATE. Amplía el CHECK
-- ut_origen_chk de core.usage_tokens (DROP + ADD con un valor más; ninguna fila deja de cumplirlo)
-- y reemplaza la función del trigger nuggets_encolar (mismo comportamiento, payload ampliado).
-- Sin BEGIN/COMMIT: el runner abre la transacción (db/migrations/README.md).
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;   -- dueño de cm.* y core.* (test_roles)

-- ---------------------------------------------------------------------------
-- 1. cm.nuggets: origen 'ticket'
-- ---------------------------------------------------------------------------
ALTER TABLE cm.nuggets
    ADD COLUMN origen    text NOT NULL DEFAULT 'documento',
    ADD COLUMN ticket_id text;

ALTER TABLE cm.nuggets ALTER COLUMN evidencia_ref DROP NOT NULL;

ALTER TABLE cm.nuggets
    ADD CONSTRAINT nuggets_origen_chk
        CHECK (origen IN ('documento', 'ticket')),
    -- Documento <=> cita un chunk. Ticket <=> nombra su ticket y no cita ningún chunk.
    ADD CONSTRAINT nuggets_origen_evidencia_chk
        CHECK ((origen = 'documento') = (evidencia_ref IS NOT NULL)
               AND (origen = 'ticket') = (ticket_id IS NOT NULL)),
    ADD CONSTRAINT nuggets_ticket_id_fkey
        FOREIGN KEY (tenant_id, ticket_id)
        REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE;

-- Cubre la FK nuggets_ticket_id_fkey y responde "¿qué nuggets salieron de este ticket?".
-- Documentado en docs/plan-indices.md.
CREATE INDEX nuggets_ticket_ix ON cm.nuggets (tenant_id, ticket_id)
    WHERE ticket_id IS NOT NULL;

-- La identidad de un nugget tampoco cambia de origen ni de ticket.
CREATE OR REPLACE FUNCTION cm.nuggets_origen_inmutable_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.origen IS DISTINCT FROM OLD.origen OR NEW.ticket_id IS DISTINCT FROM OLD.ticket_id THEN
        RAISE EXCEPTION 'cm.nuggets: el origen de un nugget no cambia (%)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER nuggets_origen_inmutable
    BEFORE UPDATE ON cm.nuggets
    FOR EACH ROW EXECUTE FUNCTION cm.nuggets_origen_inmutable_trg();

-- Mismo trigger de 041 con el origen y el ticket en el payload de la revisión.
CREATE OR REPLACE FUNCTION cm.nuggets_encolar_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
    VALUES ('cq_' || replace(gen_random_uuid()::text, '-', ''), NEW.tenant_id, 'nugget',
            jsonb_build_object(
                'nugget_id', NEW.id,
                'regla', NEW.regla,
                'tipo', NEW.tipo,
                'origen', NEW.origen,
                'ticket_id', NEW.ticket_id,
                'evidencia_ref', NEW.evidencia_ref,
                'seccion', NEW.seccion,
                'cita', NEW.cita,
                'scope_refs', NEW.scope_refs));
    RETURN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Candidatos a incidente en cm.curation_queue
-- ---------------------------------------------------------------------------
ALTER TABLE cm.curation_queue
    -- El candidato lleva su ticket y un registro con resumen (lo que exige cm.incidents).
    ADD CONSTRAINT curation_incidente_payload_chk
        CHECK (tipo <> 'incidente'
               OR (payload ? 'ticket_id'
                   AND jsonb_typeof(payload -> 'ticket_id') = 'string'
                   AND COALESCE(char_length(btrim(payload ->> 'resumen')), 0) BETWEEN 1 AND 500
                   AND jsonb_typeof(COALESCE(payload -> 'nodos', '[]'::jsonb)) = 'array')),
    -- Una revisión de incidente resuelta siempre dice quién y cuándo.
    ADD CONSTRAINT curation_incidente_revision_chk
        CHECK (tipo <> 'incidente'
               OR (estado = 'pendiente') = (revisado_por IS NULL AND revisado_en IS NULL));

-- Un ticket deja como mucho un candidato a incidente, esté pendiente o ya resuelto: reprocesar el
-- cierre no reabre lo que un curador ya rechazó. Destino del ON CONFLICT de la realimentación.
-- Documentado en docs/plan-indices.md.
CREATE UNIQUE INDEX curation_incidente_ticket_uq
    ON cm.curation_queue (tenant_id, (payload ->> 'ticket_id'))
    WHERE tipo = 'incidente';

-- Resolución humana de un candidato a incidente. SECURITY INVOKER (por defecto): corre con RLS
-- del tenant de la sesión. Al aprobar, el incidente se registra (o actualiza, uno por ticket) con
-- los textos del payload, o con los que el curador ajuste, y se enlaza a los nodos que existan.
-- El embedding lo calcula después el cliente (kallicode_codemapping.realimentacion).
-- Devuelve el id del incidente (NULL si se rechaza).
CREATE FUNCTION cm.resolver_incidente(
    p_revision_id text,
    p_usuario_id  text,
    p_decision    text,
    p_motivo      text DEFAULT NULL,
    p_resumen     text DEFAULT NULL,
    p_causa       text DEFAULT NULL,
    p_correccion  text DEFAULT NULL
) RETURNS text
    LANGUAGE plpgsql AS $$
DECLARE
    v_tenant    text := core.current_tenant();
    v_ahora     timestamptz := clock_timestamp();
    v_payload   jsonb;
    v_incidente text;
BEGIN
    IF p_decision IS NULL OR p_decision NOT IN ('aprobado', 'rechazado') THEN
        RAISE EXCEPTION 'cm.resolver_incidente: decisión inválida %', p_decision
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_usuario_id IS NULL OR btrim(p_usuario_id) = '' THEN
        RAISE EXCEPTION 'cm.resolver_incidente: la resolución exige el usuario que la toma'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT q.payload INTO v_payload
      FROM cm.curation_queue q
     WHERE q.tenant_id = v_tenant
       AND q.id = p_revision_id
       AND q.tipo = 'incidente'
       AND q.estado = 'pendiente'
     FOR UPDATE;
    IF v_payload IS NULL THEN
        RAISE EXCEPTION 'cm.resolver_incidente: % no es un candidato a incidente pendiente',
            p_revision_id
            USING ERRCODE = 'no_data_found';
    END IF;

    UPDATE cm.curation_queue
       SET estado = p_decision,
           revisado_por = p_usuario_id,
           revisado_en = v_ahora,
           payload = payload || jsonb_build_object(
               'motivo', p_motivo,
               'resumen_final', COALESCE(p_resumen, payload ->> 'resumen'))
     WHERE tenant_id = v_tenant AND id = p_revision_id;

    IF p_decision = 'rechazado' THEN
        RETURN NULL;
    END IF;

    INSERT INTO cm.incidents AS i
           (id, tenant_id, ticket_id, origen, resumen, causa, correccion, evidencia_ref,
            nodos_afectados)
    VALUES ('inc_' || replace(gen_random_uuid()::text, '-', ''), v_tenant,
            v_payload ->> 'ticket_id', 'ticket',
            COALESCE(p_resumen, v_payload ->> 'resumen'),
            COALESCE(p_causa, v_payload ->> 'causa'),
            COALESCE(p_correccion, v_payload ->> 'correccion'),
            v_payload ->> 'evidencia_ref',
            COALESCE(v_payload -> 'nodos', '[]'::jsonb))
    ON CONFLICT (tenant_id, ticket_id) WHERE ticket_id IS NOT NULL DO UPDATE
       SET origen = EXCLUDED.origen,
           resumen = EXCLUDED.resumen,
           causa = EXCLUDED.causa,
           correccion = EXCLUDED.correccion,
           evidencia_ref = EXCLUDED.evidencia_ref,
           nodos_afectados = EXCLUDED.nodos_afectados,
           actualizado_en = now()
    RETURNING i.id INTO v_incidente;

    DELETE FROM cm.incident_nodes
     WHERE tenant_id = v_tenant AND incident_id = v_incidente;
    INSERT INTO cm.incident_nodes (tenant_id, incident_id, nodo_ref)
    SELECT v_tenant, v_incidente, n.id
      FROM cm.nodes n
     WHERE n.tenant_id = v_tenant
       AND n.id IN (SELECT jsonb_array_elements_text(COALESCE(v_payload -> 'nodos', '[]'::jsonb)))
    ON CONFLICT DO NOTHING;

    UPDATE cm.curation_queue
       SET payload = payload || jsonb_build_object('incidente_id', v_incidente)
     WHERE tenant_id = v_tenant AND id = p_revision_id;

    RETURN v_incidente;
END $$;

GRANT EXECUTE ON FUNCTION cm.resolver_incidente(text, text, text, text, text, text, text)
    TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 3. cm.realimentacion_tickets: cerrojo de idempotencia por ticket
-- ---------------------------------------------------------------------------
CREATE TABLE cm.realimentacion_tickets (
    tenant_id           text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    ticket_id           text NOT NULL,
    estado              text NOT NULL DEFAULT 'en_curso',
    intentos            integer NOT NULL DEFAULT 1,
    nuggets_propuestos  integer NOT NULL DEFAULT 0,
    incidente_propuesto boolean NOT NULL DEFAULT false,
    descartados         integer NOT NULL DEFAULT 0,
    tokens_in           integer NOT NULL DEFAULT 0,
    tokens_out          integer NOT NULL DEFAULT 0,
    motivo              text,
    iniciado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now(),
    completado_en       timestamptz,
    PRIMARY KEY (tenant_id, ticket_id),
    CONSTRAINT realim_ticket_fk FOREIGN KEY (tenant_id, ticket_id)
        REFERENCES core.tickets (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT realim_estado_chk
        CHECK (estado IN ('en_curso', 'completado', 'fallido', 'sin_presupuesto')),
    CONSTRAINT realim_completado_chk CHECK ((estado = 'completado') = (completado_en IS NOT NULL)),
    CONSTRAINT realim_contadores_chk
        CHECK (intentos >= 1 AND nuggets_propuestos >= 0 AND descartados >= 0
               AND tokens_in >= 0 AND tokens_out >= 0),
    CONSTRAINT realim_motivo_len_chk CHECK (motivo IS NULL OR char_length(motivo) <= 500)
);

COMMENT ON TABLE cm.realimentacion_tickets IS
'KC-CM-10-4: una fila por ticket entregado que pasó por la realimentación del grafo. completado = ya se propusieron sus candidatos (nuggets en cm.nuggets, incidente en cm.curation_queue) y reprocesar el cierre no vuelve a llamar al LLM.';

ALTER TABLE cm.realimentacion_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.realimentacion_tickets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.realimentacion_tickets
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
GRANT SELECT, INSERT, UPDATE ON cm.realimentacion_tickets TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 4. core.usage_tokens: consumo de la realimentación
-- ---------------------------------------------------------------------------
ALTER TABLE core.usage_tokens DROP CONSTRAINT ut_origen_chk;
ALTER TABLE core.usage_tokens
    ADD CONSTRAINT ut_origen_chk
        CHECK (origen IN ('pipeline', 'codemapping_codigo', 'codemapping_docs',
                          'codemapping_feedback'));
