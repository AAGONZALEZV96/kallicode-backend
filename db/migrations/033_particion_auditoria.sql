-- ============================================================================
-- KALLICODE · 033_particion_auditoria.sql
-- KC-DB-07-1 (DB-F09, ALTO) — audit.audit_events particionada por rango mensual
-- de creado_en con pg_partman.
--
-- audit_events es la tabla que más crece del modelo y la única que no se puede
-- truncar (es la prueba). 02_indexes.sql ya anunciaba «particionar a partir de
-- ~50M de filas» sin ejecutarlo, y la clave primaria (creado_en, id) se rediseñó
-- en 017_pk_para_particion.sql precisamente para poder hacerlo. Aquí se ejecuta:
--
--   * La tabla pasa a PARTITION BY RANGE (creado_en), con una partición por mes
--     (UTC), una partición DEFAULT y doce meses futuros pre-creados.
--   * pg_partman crea la partición siguiente sin intervención manual: la
--     mantiene su worker (pg_partman_bgw) o, en su defecto, cualquier planificador
--     que ejecute CALL partman.run_maintenance_proc() (docs/particionado_auditoria.md).
--   * Los seis índices de auditoría pasan a ser por partición (se declaran en la
--     tabla padre y PostgreSQL los crea en cada partición).
--   * El trigger de sellado (trg_audit_seal), el de inmutabilidad
--     (trg_audit_immutable), la política RLS, los permisos, las restricciones y la
--     FK a core.organizations se reproducen idénticos: verify_chain, V16 y V17 no
--     cambian.
--   * No se aplica retención: la auditoría no se borra (retention queda en NULL).
--
-- Cómo se convierte: PostgreSQL no convierte una tabla existente en particionada,
-- así que la migración crea la tabla nueva, copia las filas CONSERVANDO id y sello
-- (el trigger de sellado se desactiva solo durante la copia: recalcularlo
-- rompería la cadena), verifica que no se perdió ni se alteró nada y retira la
-- tabla antigua. Todo ocurre en una transacción: si cualquier verificación falla,
-- no queda nada a medias. Durante la copia la tabla está bloqueada para escritura
-- (ver el ensayo con 5 millones de filas en docs/particionado_auditoria.md).
--
-- La reversión (particionada -> tabla simple) está en
-- db/tools/revertir_particion_auditoria.sql.
--
-- Requisito: la extensión pg_partman (00_extensiones.sql; imagen db/postgres/).
-- Nota: el ticket nombra esta migración 024_particion_auditoria.sql, pero 024
-- ya existe (cadena de auditoría); es la 033.
-- ============================================================================

SET LOCAL lock_timeout = '30s';
-- Los límites de las particiones se calculan en la zona horaria de la sesión:
-- siempre en UTC, para que no dependan de quién ejecute la migración.
SET LOCAL timezone = 'UTC';

-- ---------------------------------------------------------------------------
-- 0. Precondiciones
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        RAISE EXCEPTION
            '033: falta la extensión pg_partman. Créela con db/00_extensiones.sql (superusuario) sobre una imagen que la incluya (db/postgres/) o habilítela en el servidor (en Azure, azure.extensions) y vuelva a ejecutar.';
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'partman', 'USAGE, CREATE') THEN
        RAISE EXCEPTION
            '033: kallicode_admin no tiene permisos sobre el esquema partman. Vuelva a ejecutar db/00_extensiones.sql como superusuario.';
    END IF;
END $$;

-- Los objetos nuevos quedan a nombre de kallicode_admin, como el resto del esquema
-- (04_rls.sql). Si el runner ya es kallicode_admin esto no cambia nada.
SET LOCAL ROLE kallicode_admin;

DO $$
DECLARE
    antes bigint;
    despues bigint;
    ids_antes bigint;
    ids_despues bigint;
    sellos_antes numeric;
    sellos_despues numeric;
    desde date;
    mes date;
    r record;
BEGIN
    -- Idempotencia: si ya está particionada no hay nada que hacer.
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'audit.audit_events'::regclass) = 'p' THEN
        RAISE NOTICE '033: audit.audit_events ya está particionada; se omite.';
        RETURN;
    END IF;

    LOCK TABLE audit.audit_events IN ACCESS EXCLUSIVE MODE;

    -- Huella de la tabla antigua, para comparar al final.
    SELECT count(*), coalesce(sum(id), 0), coalesce(sum(hashtext(sello)::numeric), 0), min(creado_en)::date
      INTO antes, ids_antes, sellos_antes, desde
      FROM audit.audit_events;

    -- -----------------------------------------------------------------------
    -- 1. La tabla antigua se aparta con sus índices y restricciones renombrados
    --    (los nombres originales pasan a la tabla nueva).
    -- -----------------------------------------------------------------------
    ALTER TABLE audit.audit_events RENAME TO audit_events_pre033;
    FOR r IN
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'audit.audit_events_pre033'::regclass
    LOOP
        EXECUTE format('ALTER TABLE audit.audit_events_pre033 RENAME CONSTRAINT %I TO %I',
                       r.conname, r.conname || '_pre033');
    END LOOP;
    FOR r IN
        SELECT c.relname
          FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'audit.audit_events_pre033'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
    LOOP
        EXECUTE format('ALTER INDEX audit.%I RENAME TO %I', r.relname, r.relname || '_pre033');
    END LOOP;

    -- -----------------------------------------------------------------------
    -- 2. Tabla nueva: mismas columnas, misma clave (creado_en, id).
    -- -----------------------------------------------------------------------
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
    ) PARTITION BY RANGE (creado_en);

    COMMENT ON COLUMN audit.audit_events.ip IS
        'KC-DB-05-6: dirección de origen de la solicitud que originó el evento. Sellada desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.user_agent IS
        'KC-DB-05-6: cabecera User-Agent del cliente HTTP. Sellada desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.request_id IS
        'KC-DB-05-6: identificador de correlación de la solicitud (texto libre, p. ej. un UUID). Sellado desde la versión 2.';
    COMMENT ON COLUMN audit.audit_events.version_sello IS
        'KC-DB-05-6: formato del canónico del sello. 1 = original (sin ip/user_agent/request_id); 2 = con ellos. Lo fija el trigger de sellado.';

    -- Índices por partición: se declaran en el padre y se crean en cada partición.
    CREATE INDEX audit_tenant_fecha_ix ON audit.audit_events (tenant_id, creado_en DESC);
    CREATE INDEX audit_ticket_ix       ON audit.audit_events (tenant_id, ticket_id, creado_en);
    CREATE INDEX audit_actor_ix        ON audit.audit_events (tenant_id, actor_tipo, creado_en DESC);
    CREATE INDEX audit_evento_ix       ON audit.audit_events (tenant_id, evento);
    CREATE INDEX audit_events_sello_ix ON audit.audit_events (tenant_id, sello);
    CREATE INDEX audit_events_request_id_ix ON audit.audit_events (tenant_id, request_id)
        WHERE request_id IS NOT NULL;

    -- Triggers: se declaran en el padre y PostgreSQL los replica en cada partición.
    CREATE TRIGGER trg_audit_seal
        BEFORE INSERT ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.seal_event();
    CREATE TRIGGER trg_audit_immutable
        BEFORE UPDATE OR DELETE ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation();

    -- RLS y permisos, como en 04_rls.sql. Las particiones NO reciben permisos para
    -- la aplicación: solo se llega a ellas a través del padre, que lleva la política.
    ALTER TABLE audit.audit_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE audit.audit_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON audit.audit_events
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    REVOKE ALL ON audit.audit_events FROM PUBLIC;
    GRANT SELECT, INSERT ON audit.audit_events TO kallicode_app, kallicode_admin;

    -- -----------------------------------------------------------------------
    -- 3. El canónico del sello (029) recibe la fila como el tipo de la tabla:
    --    se redefine para el tipo de la tabla nueva (la antigua conserva el suyo
    --    hasta que se retira, más abajo).
    -- -----------------------------------------------------------------------
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

    -- -----------------------------------------------------------------------
    -- 4. pg_partman: una partición por mes (UTC), doce meses de antelación y una
    --    partición DEFAULT. Las particiones arrancan en el mes de la fila más
    --    antigua (o en el mes actual si la tabla está vacía) para que cada fila
    --    existente tenga su partición.
    -- -----------------------------------------------------------------------
    PERFORM partman.create_parent(
        p_parent_table   := 'audit.audit_events',
        p_control        := 'creado_en',
        p_interval       := '1 month',
        p_type           := 'range',
        p_premake        := 12,
        p_start_partition := to_char(date_trunc('month', coalesce(desde, current_date)::timestamp),
                                     'YYYY-MM-DD HH24:MI:SS'),
        p_default_table  := true,
        p_jobmon         := false);

    -- La auditoría no se borra: sin retención. Las particiones nuevas no heredan
    -- permisos (solo se accede por el padre).
    UPDATE partman.part_config
       SET retention = NULL, retention_keep_table = true, inherit_privileges = false,
           infinite_time_partitions = true
     WHERE parent_table = 'audit.audit_events';

    -- -----------------------------------------------------------------------
    -- 5. Copia conservando id y sello: el trigger de sellado recalcularía
    --    sello_previo/sello y rompería la cadena, y el de inmutabilidad no aplica.
    -- -----------------------------------------------------------------------
    ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal;
    -- Mes a mes: cada sentencia escribe en una sola partición (menos memoria y WAL por
    -- sentencia y mejor localidad en los índices que copiarlo todo de una vez).
    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', desde::timestamp),
                                   date_trunc('month', (SELECT max(creado_en) FROM audit.audit_events_pre033)
                                                       AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO audit.audit_events
                   (id, tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos,
                    modelo, step_id, sello_previo, sello, creado_en, ip, user_agent, request_id, version_sello)
            OVERRIDING SYSTEM VALUE
            SELECT id, tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos,
                   modelo, step_id, sello_previo, sello, creado_en, ip, user_agent, request_id, version_sello
              FROM audit.audit_events_pre033
             WHERE creado_en >= mes::timestamptz
               AND creado_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;
    ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal;

    -- La identidad continúa donde terminó la tabla antigua.
    EXECUTE format('ALTER TABLE audit.audit_events ALTER COLUMN id RESTART WITH %s',
                   coalesce((SELECT max(id) FROM audit.audit_events_pre033), 0) + 1);

    -- -----------------------------------------------------------------------
    -- 6. Verificación: misma cantidad, mismos ids, mismos sellos, nada en la
    --    partición DEFAULT. Si algo no cuadra, se revierte toda la migración.
    -- -----------------------------------------------------------------------
    SELECT count(*), coalesce(sum(id), 0), coalesce(sum(hashtext(sello)::numeric), 0)
      INTO despues, ids_despues, sellos_despues
      FROM audit.audit_events;

    IF despues <> antes OR ids_despues <> ids_antes OR sellos_despues <> sellos_antes THEN
        RAISE EXCEPTION '033: la copia no coincide (filas % -> %, ids % -> %, sellos % -> %)',
            antes, despues, ids_antes, ids_despues, sellos_antes, sellos_despues;
    END IF;
    IF EXISTS (SELECT 1 FROM ONLY audit.audit_events_default) THEN
        RAISE EXCEPTION '033: hay filas en la partición DEFAULT; falta una partición para su fecha';
    END IF;

    -- -----------------------------------------------------------------------
    -- 7. Se retira la tabla antigua (y su versión de calcular_sello).
    -- -----------------------------------------------------------------------
    DROP FUNCTION audit.calcular_sello(audit.audit_events_pre033);
    DROP TABLE audit.audit_events_pre033;

    RAISE NOTICE '033: audit.audit_events particionada (% filas copiadas).', despues;
END $$;

ANALYZE audit.audit_events;
