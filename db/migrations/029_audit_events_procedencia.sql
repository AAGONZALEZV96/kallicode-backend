-- ============================================================================
-- KALLICODE · 029_audit_events_procedencia.sql
-- KC-DB-05-6 — Procedencia de la acción en los eventos de auditoría.
--
-- audit.audit_events guardaba qué se hizo y quién lo firmó, pero no desde dónde ni
-- en qué petición, mientras core.refresh_tokens sí guarda ip inet y user_agent text.
-- Se añaden ip inet, user_agent text y request_id text, y el trigger de sellado
-- (T3, audit.seal_event) los incorpora al canónico bajo una columna version_sello
-- que identifica el formato, para no invalidar la cadena existente:
--
--   version_sello = 1  canónico original: sin los tres campos nuevos.
--   version_sello = 2  canónico con ip, user_agent y request_id.
--
-- Los eventos ya existentes quedan en versión 1 (DEFAULT 1) y siguen verificando con
-- su fórmula original; todo evento nuevo lo fija el trigger en versión 2 (la
-- aplicación no puede elegir la versión).
--
-- El canónico vive en UNA sola función, audit.calcular_sello(), que usan tanto
-- audit.seal_event como audit.verify_chain: si el formato cambiara en una y no en
-- la otra, toda la cadena parecería manipulada.
--
-- Esta migración también sanea las bases donde se aplicó una versión anterior no
-- mergeada de este ticket (028_audit_events_network_context.sql), que había dejado
-- request_id como uuid y reemplazado seal_event por otra basada en digest() de
-- pgcrypto, extensión que el proyecto no instala.
--
-- Nota: el ticket se desarrolló como 028; esa numeración la ocupa 028_cuota_delta.sql.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Columnas. IF NOT EXISTS y la conversión de uuid a text hacen que la migración
--    sea válida tanto en una base nueva como en una que aplicó la versión anterior.
-- ---------------------------------------------------------------------------
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS ip           inet;
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS user_agent   text;
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS request_id   text;
ALTER TABLE audit.audit_events ADD COLUMN IF NOT EXISTS version_sello smallint NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'audit' AND table_name = 'audit_events'
           AND column_name = 'request_id') = 'uuid' THEN
        ALTER TABLE audit.audit_events ALTER COLUMN request_id TYPE text USING request_id::text;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'audit.audit_events'::regclass
                      AND conname = 'ae_version_sello_chk') THEN
        ALTER TABLE audit.audit_events
            ADD CONSTRAINT ae_version_sello_chk CHECK (version_sello IN (1, 2)) NOT VALID;
    END IF;
END $$;

ALTER TABLE audit.audit_events VALIDATE CONSTRAINT ae_version_sello_chk;

COMMENT ON COLUMN audit.audit_events.ip IS
'KC-DB-05-6: dirección de origen de la solicitud que originó el evento. Sellada desde la versión 2.';
COMMENT ON COLUMN audit.audit_events.user_agent IS
'KC-DB-05-6: cabecera User-Agent del cliente HTTP. Sellada desde la versión 2.';
COMMENT ON COLUMN audit.audit_events.request_id IS
'KC-DB-05-6: identificador de correlación de la solicitud (texto libre, p. ej. un UUID). Sellado desde la versión 2.';
COMMENT ON COLUMN audit.audit_events.version_sello IS
'KC-DB-05-6: formato del canónico del sello. 1 = original (sin ip/user_agent/request_id); 2 = con ellos. Lo fija el trigger de sellado.';

-- ---------------------------------------------------------------------------
-- 2. Canónico del sello, en un solo lugar. La versión 1 es EXACTAMENTE la fórmula
--    original de seal_event (db/03_triggers.sql): no debe cambiar nunca. La versión 2
--    agrega ip, user_agent y request_id separados por chr(31) (separador de unidad)
--    para que un valor no pueda «prestar» caracteres al vecino.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.calcular_sello(e audit.audit_events) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        CASE e.version_sello
            WHEN 1 THEN
                e.sello_previo || e.tenant_id || e.evento || e.resumen
                || coalesce(e.datos::text, '') || coalesce(e.ticket_id, '')
                || coalesce(e.actor_id, '')
                || to_char(e.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            ELSE
                e.sello_previo || e.tenant_id || e.evento || e.resumen
                || coalesce(e.datos::text, '') || coalesce(e.ticket_id, '')
                || coalesce(e.actor_id, '')
                || chr(31) || coalesce(abbrev(e.ip), '')
                || chr(31) || coalesce(e.user_agent, '')
                || chr(31) || coalesce(e.request_id, '')
                || chr(31)
                || to_char(e.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
        END,
        'UTF8')), 'hex')
$$;

COMMENT ON FUNCTION audit.calcular_sello(audit.audit_events) IS
'KC-DB-05-6: canónico del sello según e.version_sello. Usada por seal_event y verify_chain; el formato de la versión 1 es inmutable.';

-- ---------------------------------------------------------------------------
-- 3. audit.seal_event (versión de KC-DB-05-1, 024_cadena_auditoria.sql): el único
--    cambio es que fija version_sello = 2 y calcula el sello con calcular_sello().
-- ---------------------------------------------------------------------------
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
    NEW.version_sello := 2;           -- la aplicación no elige el formato
    NEW.sello := audit.calcular_sello(NEW);

    UPDATE audit.chain_heads
       SET eventos = eventos + 1, ultimo_sello = NEW.sello
     WHERE tenant_id = NEW.tenant_id;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------
-- 4. audit.verify_chain (versión de KC-DB-05-1): el único cambio es que recalcula el
--    sello con calcular_sello(), que respeta la versión de cada evento.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.verify_chain(p_tenant text,
                                   p_desde timestamptz DEFAULT NULL,
                                   p_hasta timestamptz DEFAULT NULL)
RETURNS TABLE (integra boolean,
               eventos_verificados bigint,
               primer_evento_roto bigint,
               eventos_esperados bigint,
               eventos_observados bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    genesis constant text := repeat('0', 64);
    sin_ventana boolean := (p_desde IS NULL AND p_hasta IS NULL);
    n bigint;
    roto bigint;
BEGIN
    WITH ev AS (
        SELECT e.id,
               audit.calcular_sello(e) <> e.sello AS sello_malo,
               (e.sello_previo <> genesis AND NOT EXISTS (
                   SELECT 1 FROM audit.audit_events p
                    WHERE p.tenant_id = e.tenant_id AND p.sello = e.sello_previo
               )) AS eslabon_perdido,
               (count(*) OVER (PARTITION BY e.sello_previo) > 1) AS bifurcado
          FROM audit.audit_events e
         WHERE e.tenant_id = p_tenant
           AND (p_desde IS NULL OR e.creado_en >= p_desde)
           AND (p_hasta IS NULL OR e.creado_en <= p_hasta)
    )
    SELECT count(*), min(id) FILTER (WHERE sello_malo OR eslabon_perdido OR bifurcado)
      INTO n, roto
      FROM ev;

    eventos_verificados := n;
    eventos_observados  := n;
    primer_evento_roto  := roto;
    eventos_esperados   := NULL;
    IF sin_ventana THEN
        SELECT h.eventos INTO eventos_esperados
          FROM audit.chain_heads h WHERE h.tenant_id = p_tenant;
    END IF;

    integra := roto IS NULL
               AND (eventos_esperados IS NULL OR eventos_esperados = n);
    RETURN NEXT;
END $$;
