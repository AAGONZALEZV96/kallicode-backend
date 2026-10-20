-- ============================================================================
-- KALLICODE · db/tools/revertir_particion_auditoria.sql
-- KC-DB-07-1 — Reversión de 033_particion_auditoria.sql: audit.audit_events
-- vuelve a ser una tabla simple, conservando TODAS las filas (también las
-- escritas después de la migración), con sus ids y sus sellos.
--
-- Cuándo: solo si el particionado causa un problema que no se puede corregir
-- (docs/particionado_auditoria.md, «Reversión»). No es una migración: no entra
-- en el ledger. Tampoco hace falta restaurar nada: la cadena de sellos no se toca.
--
-- Uso (superusuario, o kallicode_admin si es el dueño de todas las particiones; con la aplicación en pausa o en una
-- ventana de bajo tráfico, porque bloquea la tabla mientras copia):
--     psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 \
--          -f db/tools/revertir_particion_auditoria.sql
--
-- Es una única transacción: si la verificación final falla, no queda nada a
-- medias. Es idempotente: si la tabla ya es simple, no hace nada.
--
-- Para volver a particionar: psql -f db/migrations/033_particion_auditoria.sql
-- (la migración detecta que la tabla no está particionada y la convierte).
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL lock_timeout = '30s';
SET LOCAL timezone = 'UTC';

DO $$
DECLARE
    antes bigint;
    despues bigint;
    ids_antes bigint;
    ids_despues bigint;
    sellos_antes numeric;
    sellos_despues numeric;
    mes date;
    r record;
BEGIN
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'audit.audit_events'::regclass) <> 'p' THEN
        RAISE NOTICE 'audit.audit_events ya es una tabla simple; no hay nada que revertir.';
        RETURN;
    END IF;

    LOCK TABLE audit.audit_events IN ACCESS EXCLUSIVE MODE;

    SELECT count(*), coalesce(sum(id), 0), coalesce(sum(hashtext(sello)::numeric), 0)
      INTO antes, ids_antes, sellos_antes
      FROM audit.audit_events;

    -- 1. pg_partman deja de gestionar la tabla.
    DELETE FROM partman.part_config WHERE parent_table = 'audit.audit_events';
    DROP TABLE IF EXISTS partman.template_audit_audit_events;

    -- 2. La tabla particionada se aparta con sus nombres renombrados.
    ALTER TABLE audit.audit_events RENAME TO audit_events_part033;
    FOR r IN
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'audit.audit_events_part033'::regclass AND conparentid = 0
    LOOP
        EXECUTE format('ALTER TABLE audit.audit_events_part033 RENAME CONSTRAINT %I TO %I',
                       r.conname, r.conname || '_part033');
    END LOOP;
    FOR r IN
        SELECT c.relname
          FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'audit.audit_events_part033'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
    LOOP
        EXECUTE format('ALTER INDEX audit.%I RENAME TO %I', r.relname, r.relname || '_part033');
    END LOOP;

    -- 3. Tabla simple: misma definición que db/01_schema.sql + 017 + 029 + 030.
    CREATE TABLE audit.audit_events (
        id                bigint GENERATED ALWAYS AS IDENTITY,
        tenant_id         text NOT NULL,
        ticket_id         text,                              -- sin FK: la auditoría sobrevive a todo
        job_id            text,
        etapa             text,
        actor_tipo        text NOT NULL,                     -- humano | agente | sistema
        actor_id          text,                              -- user_id o nombre del agente
        evento            text NOT NULL,                     -- código estable (gate_firmado, spec_generada...)
        resumen           text NOT NULL,                     -- texto visible en el portal
        datos             jsonb,                             -- payload estructurado del evento
        modelo            text,                              -- si actor agente
        step_id           text,                              -- ref a core.llm_steps
        sello_previo      text NOT NULL,
        sello             text NOT NULL,                     -- SHA-256(sello_previo || canónico)
        creado_en         timestamptz NOT NULL DEFAULT now(),
        ip                inet,
        user_agent        text,
        request_id        text,
        version_sello     smallint NOT NULL DEFAULT 1,
        CONSTRAINT audit_events_pkey PRIMARY KEY (creado_en, id),
        CONSTRAINT audit_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id),
        CONSTRAINT ae_actor_chk CHECK (actor_tipo IN ('humano','agente','sistema')),
        CONSTRAINT ae_sello_chk CHECK (sello ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ae_version_sello_chk CHECK (version_sello IN (1, 2))
    );

    COMMENT ON COLUMN audit.audit_events.ip IS
        'KC-DB-05-6: dirección de origen de la solicitud que originó el evento. Sellada desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.user_agent IS
        'KC-DB-05-6: cabecera User-Agent del cliente HTTP. Sellada desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.request_id IS
        'KC-DB-05-6: identificador de correlación de la solicitud (texto libre, p. ej. un UUID). Sellado desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.version_sello IS
        'KC-DB-05-6: formato del canónico del sello. 1 = original (sin ip/user_agent/request_id); 2 = con ellos. Lo fija el trigger de sellado.';

    CREATE INDEX audit_tenant_fecha_ix ON audit.audit_events (tenant_id, creado_en DESC);
    CREATE INDEX audit_ticket_ix       ON audit.audit_events (tenant_id, ticket_id, creado_en);
    CREATE INDEX audit_actor_ix        ON audit.audit_events (tenant_id, actor_tipo, creado_en DESC);
    CREATE INDEX audit_evento_ix       ON audit.audit_events (tenant_id, evento);
    CREATE INDEX audit_events_sello_ix ON audit.audit_events (tenant_id, sello);
    CREATE INDEX audit_events_request_id_ix ON audit.audit_events (tenant_id, request_id)
        WHERE request_id IS NOT NULL;

    CREATE TRIGGER trg_audit_seal
        BEFORE INSERT ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.seal_event();
    CREATE TRIGGER trg_audit_immutable
        BEFORE UPDATE OR DELETE ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation();

    ALTER TABLE audit.audit_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE audit.audit_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON audit.audit_events
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    REVOKE ALL ON audit.audit_events FROM PUBLIC;
    GRANT SELECT, INSERT ON audit.audit_events TO kallicode_app, kallicode_admin;
    ALTER TABLE audit.audit_events OWNER TO kallicode_admin;      -- dueño del esquema, como en 04_rls.sql

    -- 4. calcular_sello recibe la fila como el tipo de la tabla: se redefine para el tipo nuevo.
    CREATE OR REPLACE FUNCTION audit.calcular_sello(e audit.audit_events) RETURNS text
    LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $f$
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
    $f$;
    COMMENT ON FUNCTION audit.calcular_sello(audit.audit_events) IS
        'KC-DB-05-6: canónico del sello según e.version_sello. Usada por seal_event y verify_chain; el formato de la versión 1 es inmutable.';
    ALTER FUNCTION audit.calcular_sello(audit.audit_events) OWNER TO kallicode_admin;

    -- 5. Copia conservando id y sello (el trigger de sellado los recalcularía y rompería la cadena).
    ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal;
    -- Mes a mes: una sola sentencia con millones de filas satura la memoria y el WAL.
    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', (SELECT min(creado_en) FROM audit.audit_events_part033)
                                                        AT TIME ZONE 'UTC'),
                                   date_trunc('month', (SELECT max(creado_en) FROM audit.audit_events_part033)
                                                        AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO audit.audit_events
                   (id, tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos,
                    modelo, step_id, sello_previo, sello, creado_en, ip, user_agent, request_id, version_sello)
            OVERRIDING SYSTEM VALUE
            SELECT id, tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos,
                   modelo, step_id, sello_previo, sello, creado_en, ip, user_agent, request_id, version_sello
              FROM audit.audit_events_part033
             WHERE creado_en >= mes::timestamptz
               AND creado_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;
    ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal;

    EXECUTE format('ALTER TABLE audit.audit_events ALTER COLUMN id RESTART WITH %s',
                   coalesce((SELECT max(id) FROM audit.audit_events_part033), 0) + 1);

    -- 6. Verificación: misma cantidad, mismos ids, mismos sellos.
    SELECT count(*), coalesce(sum(id), 0), coalesce(sum(hashtext(sello)::numeric), 0)
      INTO despues, ids_despues, sellos_despues
      FROM audit.audit_events;
    IF despues <> antes OR ids_despues <> ids_antes OR sellos_despues <> sellos_antes THEN
        RAISE EXCEPTION 'reversión: la copia no coincide (filas % -> %, ids % -> %, sellos % -> %)',
            antes, despues, ids_antes, ids_despues, sellos_antes, sellos_despues;
    END IF;

    -- 7. Se retira la tabla particionada (con sus particiones) y su versión de calcular_sello.
    DROP FUNCTION audit.calcular_sello(audit.audit_events_part033);
    DROP TABLE audit.audit_events_part033;

    RAISE NOTICE 'audit.audit_events revertida a tabla simple (% filas).', despues;
END $$;

COMMIT;
ANALYZE audit.audit_events;
