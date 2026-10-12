-- ============================================================================
-- KALLICODE · 029_audit_events_contexto_red.sql
-- KC-DB-05-6 — Agregar ip, user_agent y request_id a audit.audit_events.
--
-- Agrega tres columnas opcionales con el contexto de red de la solicitud que
-- originó el evento: la dirección de origen, el User-Agent del cliente HTTP y un
-- identificador de correlación. audit.registrar_evento (kallicode_core.auditoria)
-- ya las rellena.
--
-- Estas columnas NO forman parte del sello de la cadena: seal_event y verify_chain
-- no cambian de comportamiento y los eventos anteriores siguen verificando. Si más
-- adelante se decide sellarlas, se hace con otra migración que actualice seal_event
-- y verify_chain a la vez (cualquier cambio del canónico del sello debe tocar ambas).
--
-- Se redefine audit.seal_event con la versión de KC-DB-05-1 (024_cadena_auditoria.sql,
-- sin cambios) para sanear las bases donde se aplicó una versión anterior de esta
-- migración (028_audit_events_network_context.sql, nunca mergeada), que la había
-- reemplazado por otra basada en digest() de pgcrypto, extensión que el proyecto no
-- instala.
--
-- Nota: el ticket se desarrolló como 028; esa numeración la ocupa 028_cuota_delta.sql.
-- ============================================================================

ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS ip         inet;
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS user_agent text;
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS request_id uuid;

COMMENT ON COLUMN audit.audit_events.ip IS
'KC-DB-05-6: dirección de origen de la solicitud que originó el evento. No forma parte del sello.';
COMMENT ON COLUMN audit.audit_events.user_agent IS
'KC-DB-05-6: cabecera User-Agent del cliente HTTP. No forma parte del sello.';
COMMENT ON COLUMN audit.audit_events.request_id IS
'KC-DB-05-6: identificador UUID de correlación de la solicitud. No forma parte del sello.';

-- audit.seal_event: idéntica a 024_cadena_auditoria.sql (ver nota de la cabecera).
CREATE OR REPLACE FUNCTION audit.seal_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE prev text;
BEGIN
    -- La función corre con privilegios de su dueño: no puede sellar eventos de
    -- otro tenant aunque el INSERT lo pida (la política RLS lo rechazaría, pero
    -- solo después de haber tocado la cabeza ajena).
    IF coalesce(core.current_tenant(), '') <> ''
       AND NEW.tenant_id <> core.current_tenant() THEN
        RAISE EXCEPTION 'audit.seal_event: el evento es de otro tenant (%)', NEW.tenant_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    INSERT INTO audit.chain_heads (tenant_id) VALUES (NEW.tenant_id)
        ON CONFLICT (tenant_id) DO NOTHING;
    -- Bloquea la cabeza de cadena del tenant hasta el COMMIT:
    SELECT ultimo_sello INTO prev
      FROM audit.chain_heads WHERE tenant_id = NEW.tenant_id FOR UPDATE;

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
       SET eventos = eventos + 1, ultimo_sello = NEW.sello
     WHERE tenant_id = NEW.tenant_id;
    RETURN NEW;
END $$;
