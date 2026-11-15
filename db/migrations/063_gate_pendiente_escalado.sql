-- Migración 063: gate_pendiente al escalar a una persona (KC-BE-06-3, cierra BE-F39).
--
-- T9 (core.mirror_ticket_stage, db/03_triggers.sql) copia el estado del job en el
-- ticket, pero ante 'escalado_humano' no tocaba el ticket: un job escalado desde un
-- gate (devoluciones sobre MAX_ITERACIONES) dejaba tickets.gate_pendiente en 1, 2 o 3.
-- El ticket seguía en Aprobaciones y en el badge como si esperara una firma que ya no
-- se puede dar. Ahora el escalado conserva la etapa visible y limpia gate_pendiente.
-- El resto de los estados se comporta igual que antes.

SET lock_timeout = '5s';
SET statement_timeout = '30s';

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
    ELSIF NEW.estado = 'escalado_humano' THEN
        -- Nadie puede firmar un gate de un job escalado: el ticket deja de esperarlo.
        UPDATE core.tickets
           SET gate_pendiente = NULL
         WHERE id = NEW.ticket_id
           AND gate_pendiente IS NOT NULL;
    END IF;
    RETURN NEW;
END $$;

-- Tickets que ya quedaron con un gate pendiente y el job escalado.
UPDATE core.tickets t
   SET gate_pendiente = NULL
  FROM core.jobs j
 WHERE j.ticket_id = t.id
   AND j.estado = 'escalado_humano'
   AND t.gate_pendiente IS NOT NULL;
