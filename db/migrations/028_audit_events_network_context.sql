-- Migración DDL: KC-DB-05-6
-- Agrega columnas de telemetría de red a audit.audit_events

-- 1. Agregar columnas opcionales a audit.audit_events
ALTER TABLE audit.audit_events ADD COLUMN ip inet;
ALTER TABLE audit.audit_events ADD COLUMN user_agent text;
ALTER TABLE audit.audit_events ADD COLUMN request_id uuid;

-- 2. Actualizar la función de sellado audit.seal_event()
-- Incorpora ip, user_agent y request_id en la cadena canónica que genera el sello SHA-256
CREATE OR REPLACE FUNCTION audit.seal_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_sello_previo text;
BEGIN
    -- Obtener el último sello de la cadena para el tenant (con bloqueo de fila)
    SELECT ultimo_sello INTO v_sello_previo
      FROM audit.chain_heads
     WHERE tenant_id = NEW.tenant_id
     FOR UPDATE;

    IF v_sello_previo IS NULL THEN
        -- Si no existe la cabeza de cadena, inicializar con génesis
        INSERT INTO audit.chain_heads (tenant_id, ultimo_sello, eventos)
        VALUES (NEW.tenant_id, repeat('0', 64), 0)
        RETURNING ultimo_sello INTO v_sello_previo;
    END IF;

    NEW.sello_previo := v_sello_previo;

    -- Calcular el sello canónico SHA-256
    NEW.sello := encode(digest(
        NEW.sello_previo ||
        NEW.tenant_id ||
        NEW.evento ||
        NEW.resumen ||
        coalesce(NEW.datos::text, '') ||
        coalesce(NEW.ticket_id, '') ||
        coalesce(NEW.actor_id, '') ||
        coalesce(host(NEW.ip), '') ||
        coalesce(NEW.user_agent, '') ||
        coalesce(NEW.request_id::text, '') ||
        to_char(NEW.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'sha256'
    ), 'hex');

    -- Actualizar el puntero en chain_heads
    UPDATE audit.chain_heads
       SET ultimo_sello = NEW.sello,
           eventos = eventos + 1
     WHERE tenant_id = NEW.tenant_id;

    RETURN NEW;
END;
$$;