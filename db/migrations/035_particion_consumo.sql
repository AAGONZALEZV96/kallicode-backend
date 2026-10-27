-- ============================================================================
-- KALLICODE · 035_particion_consumo.sql
-- KC-DB-07-2 (DB-F09, ALTO) — core.usage_tokens y core.job_transitions
-- particionadas por rango mensual de creado_en con pg_partman, siguiendo el
-- patrón de 033_particion_auditoria.sql (KC-DB-07-1).
--
--   * usage_tokens acumula una fila por invocación de modelo y job_transitions una
--     por cambio de estado de job: las dos crecen sin techo y rotan mucho. Sus
--     claves primarias ya incluían creado_en (017_pk_para_particion.sql).
--   * Cada tabla pasa a PARTITION BY RANGE (creado_en): una partición por mes
--     (UTC), doce meses futuros pre-creados y una partición DEFAULT. pg_partman
--     crea la siguiente sin intervención manual (pg_partman_bgw o cualquier
--     planificador que ejecute CALL partman.run_maintenance_proc()).
--   * Las claves ajenas compuestas del Sprint 3 siguen declaradas y validadas
--     (en la tabla padre y en cada partición): usage_tokens -> tickets(tenant_id, id)
--     y job_transitions -> jobs(tenant_id, id), ambas con ON DELETE CASCADE.
--   * Los índices (3 y 3), los CHECK, la FK a core.organizations, la política RLS,
--     los permisos y el trigger de solo-añadir de job_transitions se reproducen
--     idénticos. No se aplica retención (la fijará core.retention_policies, 034).
--   * Autovacuum por tabla (§9.3), fijado aquí y no en el servidor: ver la sección 3.
--
-- Cómo se convierte (igual que 033): se crea la tabla nueva, se copia mes a mes
-- conservando id, se verifica (filas, ids y una huella del contenido) y se retira
-- la antigua. Una transacción por archivo: si algo no cuadra, no queda nada a
-- medias. Durante la copia cada tabla está bloqueada para escritura. Idempotente.
--
-- Requisito: pg_partman (00_extensiones.sql; imagen db/postgres/).
-- Nota: el ticket nombra esta migración 025_particion_consumo.sql, pero 025 ya
-- existe (append-only de las probatorias) y la 034 la tomó retention_policies.
-- ============================================================================

SET LOCAL lock_timeout = '30s';
SET LOCAL timezone = 'UTC';   -- límites de partición siempre en UTC

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        RAISE EXCEPTION
            '035: falta la extensión pg_partman. Créela con db/00_extensiones.sql (superusuario) sobre una imagen que la incluya (db/postgres/) o habilítela en el servidor (en Azure, azure.extensions) y vuelva a ejecutar.';
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'partman', 'USAGE, CREATE') THEN
        RAISE EXCEPTION
            '035: kallicode_admin no tiene permisos sobre el esquema partman. Vuelva a ejecutar db/00_extensiones.sql como superusuario.';
    END IF;
END $$;

-- Las particiones no heredan la política RLS del padre: la aplicación no debe tener acceso a ellas
-- (04_rls.sql concede por defecto permisos sobre toda tabla nueva de core). El event trigger de
-- 00_extensiones.sql se los quita en cuanto se crean; sin él, las particiones que cree pg_partman
-- cada mes quedarían legibles para la aplicación sin filtro de tenant.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'kc_particiones_sin_acceso' AND evtenabled <> 'D') THEN
        RAISE EXCEPTION
            '035: falta el event trigger kc_particiones_sin_acceso. Ejecute db/00_extensiones.sql como superusuario (si el servidor no permite event triggers, no se puede particionar tablas con RLS en core).';
    END IF;
END $$;

-- Los objetos nuevos quedan a nombre de kallicode_admin, como el resto del esquema.
SET LOCAL ROLE kallicode_admin;

-- ============================================================================
-- 1. core.usage_tokens
-- ============================================================================
DO $$
DECLARE
    antes bigint;
    despues bigint;
    ids_antes bigint;
    ids_despues bigint;
    huella_antes numeric;
    huella_despues numeric;
    desde date;
    mes date;
BEGIN
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'core.usage_tokens'::regclass) = 'p' THEN
        RAISE NOTICE '035: core.usage_tokens ya está particionada; se omite.';
        RETURN;
    END IF;

    LOCK TABLE core.usage_tokens IN ACCESS EXCLUSIVE MODE;

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || ticket_id || etapa || modelo || tier || tokens_in || tokens_out
                                 || invocaciones || duracion_ms || creado_en::text)::numeric), 0),
           min(creado_en)::date
      INTO antes, ids_antes, huella_antes, desde
      FROM core.usage_tokens;

    -- La tabla antigua se aparta con sus índices y restricciones renombrados.
    ALTER TABLE core.usage_tokens RENAME TO usage_tokens_pre035;
    ALTER TABLE core.usage_tokens_pre035 RENAME CONSTRAINT usage_tokens_pkey TO usage_tokens_pkey_pre035;
    ALTER TABLE core.usage_tokens_pre035 RENAME CONSTRAINT usage_tokens_tenant_id_fkey TO usage_tokens_tenant_id_fkey_pre035;
    ALTER TABLE core.usage_tokens_pre035 RENAME CONSTRAINT usage_tokens_ticket_id_fkey TO usage_tokens_ticket_id_fkey_pre035;
    ALTER TABLE core.usage_tokens_pre035 RENAME CONSTRAINT ut_pos_chk TO ut_pos_chk_pre035;
    ALTER TABLE core.usage_tokens_pre035 RENAME CONSTRAINT ut_tier_chk TO ut_tier_chk_pre035;
    ALTER INDEX core.idx_usage_tokens_tenant_ticket_id RENAME TO idx_usage_tokens_tenant_ticket_id_pre035;
    ALTER INDEX core.usage_tokens_tenant_ix RENAME TO usage_tokens_tenant_ix_pre035;
    ALTER INDEX core.usage_tokens_tenant_ticket_etapa_ix RENAME TO usage_tokens_tenant_ticket_etapa_ix_pre035;

    CREATE TABLE core.usage_tokens (
        id                bigint GENERATED ALWAYS AS IDENTITY,
        tenant_id         text NOT NULL,
        ticket_id         text NOT NULL,
        etapa             text NOT NULL,
        modelo            text NOT NULL,
        tier              text NOT NULL,
        invocaciones      integer NOT NULL DEFAULT 1,
        tokens_in         integer NOT NULL,
        tokens_out        integer NOT NULL,
        duracion_ms       integer NOT NULL,
        creado_en         timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT usage_tokens_pkey PRIMARY KEY (creado_en, id),
        CONSTRAINT usage_tokens_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id),
        CONSTRAINT usage_tokens_ticket_id_fkey FOREIGN KEY (tenant_id, ticket_id)
            REFERENCES core.tickets(tenant_id, id) ON DELETE CASCADE,
        CONSTRAINT ut_pos_chk CHECK (tokens_in >= 0 AND tokens_out >= 0),
        CONSTRAINT ut_tier_chk CHECK (tier IN ('flash', 'pro', 'fable'))
    ) PARTITION BY RANGE (creado_en);

    CREATE INDEX idx_usage_tokens_tenant_ticket_id    ON core.usage_tokens (tenant_id, ticket_id);
    CREATE INDEX usage_tokens_tenant_ix               ON core.usage_tokens (tenant_id, creado_en);
    CREATE INDEX usage_tokens_tenant_ticket_etapa_ix  ON core.usage_tokens (tenant_id, ticket_id, etapa);

    ALTER TABLE core.usage_tokens ENABLE ROW LEVEL SECURITY;
    ALTER TABLE core.usage_tokens FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON core.usage_tokens
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    REVOKE ALL ON core.usage_tokens FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE, DELETE ON core.usage_tokens TO kallicode_app;

    PERFORM partman.create_parent(
        p_parent_table    := 'core.usage_tokens',
        p_control         := 'creado_en',
        p_interval        := '1 month',
        p_type            := 'range',
        p_premake         := 12,
        p_start_partition := to_char(date_trunc('month', coalesce(desde, current_date)::timestamp),
                                     'YYYY-MM-DD HH24:MI:SS'),
        p_default_table   := true,
        p_jobmon          := false);
    UPDATE partman.part_config
       SET retention = NULL, retention_keep_table = true, inherit_privileges = false,
           infinite_time_partitions = true
     WHERE parent_table = 'core.usage_tokens';

    -- Copia mes a mes (una sola partición por sentencia), conservando id.
    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', desde::timestamp),
                                   date_trunc('month', (SELECT max(creado_en) FROM core.usage_tokens_pre035)
                                                       AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO core.usage_tokens
                   (id, tenant_id, ticket_id, etapa, modelo, tier, invocaciones, tokens_in, tokens_out,
                    duracion_ms, creado_en)
            OVERRIDING SYSTEM VALUE
            SELECT id, tenant_id, ticket_id, etapa, modelo, tier, invocaciones, tokens_in, tokens_out,
                   duracion_ms, creado_en
              FROM core.usage_tokens_pre035
             WHERE creado_en >= mes::timestamptz
               AND creado_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;
    EXECUTE format('ALTER TABLE core.usage_tokens ALTER COLUMN id RESTART WITH %s',
                   coalesce((SELECT max(id) FROM core.usage_tokens_pre035), 0) + 1);

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || ticket_id || etapa || modelo || tier || tokens_in || tokens_out
                                 || invocaciones || duracion_ms || creado_en::text)::numeric), 0)
      INTO despues, ids_despues, huella_despues
      FROM core.usage_tokens;
    IF despues <> antes OR ids_despues <> ids_antes OR huella_despues <> huella_antes THEN
        RAISE EXCEPTION '035: usage_tokens: la copia no coincide (filas % -> %, ids % -> %, huella % -> %)',
            antes, despues, ids_antes, ids_despues, huella_antes, huella_despues;
    END IF;
    IF EXISTS (SELECT 1 FROM ONLY core.usage_tokens_default) THEN
        RAISE EXCEPTION '035: usage_tokens: hay filas en la partición DEFAULT; falta una partición para su fecha';
    END IF;

    DROP TABLE core.usage_tokens_pre035;
    RAISE NOTICE '035: core.usage_tokens particionada (% filas copiadas).', despues;
END $$;

-- ============================================================================
-- 2. core.job_transitions
-- ============================================================================
DO $$
DECLARE
    antes bigint;
    despues bigint;
    ids_antes bigint;
    ids_despues bigint;
    huella_antes numeric;
    huella_despues numeric;
    desde date;
    mes date;
BEGIN
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'core.job_transitions'::regclass) = 'p' THEN
        RAISE NOTICE '035: core.job_transitions ya está particionada; se omite.';
        RETURN;
    END IF;

    LOCK TABLE core.job_transitions IN ACCESS EXCLUSIVE MODE;

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || job_id || de_estado || a_estado || coalesce(motivo, '')
                                 || coalesce(contexto::text, '') || actor || creado_en::text)::numeric), 0),
           min(creado_en)::date
      INTO antes, ids_antes, huella_antes, desde
      FROM core.job_transitions;

    ALTER TABLE core.job_transitions RENAME TO job_transitions_pre035;
    ALTER TABLE core.job_transitions_pre035 RENAME CONSTRAINT job_transitions_pkey TO job_transitions_pkey_pre035;
    ALTER TABLE core.job_transitions_pre035 RENAME CONSTRAINT job_transitions_tenant_id_fkey TO job_transitions_tenant_id_fkey_pre035;
    ALTER TABLE core.job_transitions_pre035 RENAME CONSTRAINT job_transitions_job_id_fkey TO job_transitions_job_id_fkey_pre035;
    ALTER INDEX core.idx_job_transitions_tenant_job_id RENAME TO idx_job_transitions_tenant_job_id_pre035;
    ALTER INDEX core.job_transitions_tenant_job_ix RENAME TO job_transitions_tenant_job_ix_pre035;

    CREATE TABLE core.job_transitions (
        id                bigint GENERATED ALWAYS AS IDENTITY,
        tenant_id         text NOT NULL,
        job_id            text NOT NULL,
        de_estado         text NOT NULL,
        a_estado          text NOT NULL,
        motivo            text,
        contexto          jsonb,
        actor             text NOT NULL DEFAULT 'sistema',   -- sistema | svc:<agente> | user:<id>
        creado_en         timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT job_transitions_pkey PRIMARY KEY (creado_en, id),
        CONSTRAINT job_transitions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id),
        CONSTRAINT job_transitions_job_id_fkey FOREIGN KEY (tenant_id, job_id)
            REFERENCES core.jobs(tenant_id, id) ON DELETE CASCADE
    ) PARTITION BY RANGE (creado_en);

    CREATE INDEX idx_job_transitions_tenant_job_id ON core.job_transitions (tenant_id, job_id);
    CREATE INDEX job_transitions_tenant_job_ix     ON core.job_transitions (tenant_id, job_id, creado_en);

    -- Solo-añadir (025_append_only_probatorias.sql): el trigger se declara en el padre y
    -- PostgreSQL lo replica en cada partición.
    CREATE TRIGGER trg_probatoria_append_only
        BEFORE UPDATE OR DELETE ON core.job_transitions
        FOR EACH ROW EXECUTE FUNCTION core.probatoria_append_only();

    ALTER TABLE core.job_transitions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE core.job_transitions FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON core.job_transitions
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    REVOKE ALL ON core.job_transitions FROM PUBLIC;
    GRANT SELECT, INSERT ON core.job_transitions TO kallicode_app;
    -- Los permisos por defecto de core (04_rls.sql) le dieron también UPDATE y DELETE: 025 los retira.
    REVOKE UPDATE, DELETE ON core.job_transitions FROM kallicode_app;

    PERFORM partman.create_parent(
        p_parent_table    := 'core.job_transitions',
        p_control         := 'creado_en',
        p_interval        := '1 month',
        p_type            := 'range',
        p_premake         := 12,
        p_start_partition := to_char(date_trunc('month', coalesce(desde, current_date)::timestamp),
                                     'YYYY-MM-DD HH24:MI:SS'),
        p_default_table   := true,
        p_jobmon          := false);
    UPDATE partman.part_config
       SET retention = NULL, retention_keep_table = true, inherit_privileges = false,
           infinite_time_partitions = true
     WHERE parent_table = 'core.job_transitions';

    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', desde::timestamp),
                                   date_trunc('month', (SELECT max(creado_en) FROM core.job_transitions_pre035)
                                                       AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO core.job_transitions
                   (id, tenant_id, job_id, de_estado, a_estado, motivo, contexto, actor, creado_en)
            OVERRIDING SYSTEM VALUE
            SELECT id, tenant_id, job_id, de_estado, a_estado, motivo, contexto, actor, creado_en
              FROM core.job_transitions_pre035
             WHERE creado_en >= mes::timestamptz
               AND creado_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;
    EXECUTE format('ALTER TABLE core.job_transitions ALTER COLUMN id RESTART WITH %s',
                   coalesce((SELECT max(id) FROM core.job_transitions_pre035), 0) + 1);

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || job_id || de_estado || a_estado || coalesce(motivo, '')
                                 || coalesce(contexto::text, '') || actor || creado_en::text)::numeric), 0)
      INTO despues, ids_despues, huella_despues
      FROM core.job_transitions;
    IF despues <> antes OR ids_despues <> ids_antes OR huella_despues <> huella_antes THEN
        RAISE EXCEPTION '035: job_transitions: la copia no coincide (filas % -> %, ids % -> %, huella % -> %)',
            antes, despues, ids_antes, ids_despues, huella_antes, huella_despues;
    END IF;
    IF EXISTS (SELECT 1 FROM ONLY core.job_transitions_default) THEN
        RAISE EXCEPTION '035: job_transitions: hay filas en la partición DEFAULT; falta una partición para su fecha';
    END IF;

    DROP TABLE core.job_transitions_pre035;
    RAISE NOTICE '035: core.job_transitions particionada (% filas copiadas).', despues;
END $$;

-- ============================================================================
-- 3. Autovacuum por tabla (§9.3)
--
-- Los parámetros de almacenamiento (y por tanto los de autovacuum) no se pueden fijar en una
-- tabla particionada: se fijan en cada partición. pg_partman copia a cada partición NUEVA los
-- de su tabla plantilla; aquí se fijan en la plantilla y en las particiones que ya existen.
--
--   usage_tokens      scale_factor 0,02  · insert_scale_factor 0,02 · analyze_scale_factor 0,01
--   job_transitions   scale_factor 0,05  · insert_scale_factor 0,05 · analyze_scale_factor 0,02
--
-- Por qué: son tablas de inserción continua (una fila por invocación de modelo y una por cambio
-- de estado de job) que se leen justo después de escribirse. Con el 20 % por defecto, el
-- autovacuum de una partición caliente espera a acumular una quinta parte de filas nuevas, y
-- hasta entonces el mapa de visibilidad (index-only scans), las estadísticas y el congelado de
-- filas quedan atrasados; con 2-5 % reacciona antes y en tandas más pequeñas. usage_tokens rota
-- más (varias filas por paso de cada ticket), por eso lleva el valor más estricto. Las
-- particiones de meses pasados ya no reciben escrituras: el autovacuum las deja en paz una vez
-- congeladas. Si el volumen cambia, se ajustan aquí y en la plantilla, no en el servidor.
-- ============================================================================
DO $$
DECLARE
    t record;
    p record;
    opciones text;
BEGIN
    FOR t IN
        SELECT * FROM (VALUES
            ('core.usage_tokens',    '0.02', '0.02', '0.01'),
            ('core.job_transitions', '0.05', '0.05', '0.02')
        ) AS v(tabla, sf, isf, asf)
    LOOP
        opciones := format('autovacuum_vacuum_scale_factor = %s, autovacuum_vacuum_insert_scale_factor = %s, autovacuum_analyze_scale_factor = %s',
                           t.sf, t.isf, t.asf);
        -- Plantilla de pg_partman: la heredan las particiones que cree el mantenimiento.
        EXECUTE format('ALTER TABLE %s SET (%s)',
                       (SELECT template_table FROM partman.part_config WHERE parent_table = t.tabla), opciones);
        -- Particiones existentes (incluida la DEFAULT).
        FOR p IN
            SELECT i.inhrelid::regclass::text AS particion
              FROM pg_inherits i WHERE i.inhparent = t.tabla::regclass
        LOOP
            EXECUTE format('ALTER TABLE %s SET (%s)', p.particion, opciones);
        END LOOP;
    END LOOP;
END $$;

-- ============================================================================
-- 4. V21 y las particiones
--
-- core.incoherencias_tenant() (013) recorre TODAS las claves ajenas de pg_constraint. Una tabla
-- particionada reproduce cada FK en cada partición (constraint con conparentid <> 0), y la
-- función las seguiría con SELECT directo sobre cada partición: duplicaría el trabajo y, al
-- ejecutarla la aplicación (V21 la llama kallicode_app), fallaría con «permission denied» porque
-- la aplicación no tiene acceso a las particiones. Se excluyen las FK heredadas: la de la tabla
-- padre ya cubre todas sus filas, porque consultar el padre recorre las particiones.
-- Es la definición de 013 con una sola condición añadida (c.conparentid = 0).
-- ============================================================================
CREATE OR REPLACE FUNCTION core.incoherencias_tenant()
RETURNS TABLE (relacion text, tabla_hija text, tabla_padre text,
               fila jsonb, tenant_hija text, tenant_padre text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.conname,
               c.conrelid::regclass  AS hija,
               c.confrelid::regclass AS padre,
               -- Unión hija/padre por las columnas de la FK, sin la pareja de tenant:
               (SELECT string_agg(format('p.%I = h.%I', pa.attname, ha.attname), ' AND ')
                  FROM unnest(c.conkey, c.confkey) AS k(hijo_n, padre_n)
                  JOIN pg_attribute ha ON ha.attrelid = c.conrelid  AND ha.attnum = k.hijo_n
                  JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.padre_n
                 WHERE NOT (ha.attname = 'tenant_id' AND pa.attname = 'tenant_id')
               ) AS union_sql,
               -- Clave primaria de la hija como jsonb, para identificar la fila:
               (SELECT string_agg(format('%L, h.%I', a.attname, a.attname), ', ')
                  FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = c.conrelid AND i.indisprimary
               ) AS pk_sql
          FROM pg_constraint c
         WHERE c.contype = 'f'
           AND c.conparentid = 0            -- KC-DB-07-2: no las FK clonadas en cada partición
           -- la hija y el padre tienen tenant_id (escalar o compuesta, da igual)
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.conrelid AND attname = 'tenant_id' AND NOT attisdropped)
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.confrelid AND attname = 'tenant_id' AND NOT attisdropped)
         ORDER BY c.conrelid::regclass::text, c.conname
    LOOP
        CONTINUE WHEN r.union_sql IS NULL;  -- FK solo por tenant_id: nada que comparar
        RETURN QUERY EXECUTE format(
            'SELECT %L::text, %L::text, %L::text, %s, h.tenant_id::text, p.tenant_id::text
               FROM %s h JOIN %s p ON %s
              WHERE h.tenant_id <> p.tenant_id',
            r.conname, r.hija::text, r.padre::text,
            CASE WHEN r.pk_sql IS NULL THEN 'jsonb_build_object(''ctid'', h.ctid::text)'
                 ELSE 'jsonb_build_object(' || r.pk_sql || ')' END,
            r.hija, r.padre, r.union_sql);
    END LOOP;
END $$;

COMMENT ON FUNCTION core.incoherencias_tenant() IS
'KC-DB-03-3/03-2: filas hijas cuyo tenant_id no coincide con el de su padre, en toda FK
(escalar o compuesta) entre dos tablas con tenant_id, generadas desde pg_constraint.
Base de V21 y consulta de reparación. KC-DB-07-2: se omiten las FK clonadas en las
particiones (conparentid <> 0): la de la tabla padre ya las cubre.';

-- ============================================================================
-- 4. Comprobación final: la aplicación no tiene ningún permiso sobre las particiones
-- ============================================================================
DO $$
DECLARE
    expuestas text;
BEGIN
    SELECT string_agg(c.oid::regclass::text, ', ') INTO expuestas
      FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
     WHERE i.inhparent IN ('core.usage_tokens'::regclass, 'core.job_transitions'::regclass)
       AND (has_table_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE, DELETE')
            OR has_any_column_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE'));
    IF expuestas IS NOT NULL THEN
        RAISE EXCEPTION '035: particiones con permisos para kallicode_app (se saltarían la RLS del padre): %', expuestas;
    END IF;
END $$;

ANALYZE core.usage_tokens;
ANALYZE core.job_transitions;
