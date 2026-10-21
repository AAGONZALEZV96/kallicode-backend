-- ============================================================================
-- KALLICODE · 036_particion_colas.sql
-- KC-DB-07-3 (DB-F09, ALTO) — core.webhook_inbox y core.notifications
-- particionadas por rango mensual con pg_partman, y purga por partición.
--
--   * webhook_inbox guarda el payload crudo de cada entrega; notifications una fila por
--     aviso y usuario. El §9.3 fija 90 días de retención para los webhooks procesados.
--     Con la tabla simple la purga era un DELETE masivo (housekeeping.py) que deja bloat;
--     particionadas, vencer un mes es un DROP de la partición: instantáneo y sin bloat.
--   * Cada tabla pasa a PARTITION BY RANGE: una partición por mes, doce meses futuros
--     pre-creados y una partición DEFAULT; pg_partman crea la siguiente sin intervención.
--   * Índices (4 y 4), CHECK, claves ajenas compuestas, RLS (incluida la política
--     worker_colas), permisos y la lógica de las colas quedan idénticos. El índice parcial de
--     la cola (webhook_inbox_pend_ix) pasa a ser uno por partición.
--   * core.purgar_particiones_vencidas() elimina (DROP) las particiones vencidas, nunca hace
--     DELETE, y NO elimina una partición de webhook_inbox con entregas 'pendiente' (sección 4).
--
-- IDEMPOTENCIA DE LAS ENTREGAS (wi_delivery_uq). Una restricción UNIQUE de una tabla
-- particionada debe incluir la clave de partición. Si la clave fuera recibido_en (un instante
-- distinto en cada entrega), UNIQUE (connection_id, delivery_id, recibido_en) NUNCA se violaría
-- y la restricción dejaría de deduplicar. Por eso webhook_inbox se particiona por una columna
-- NUEVA, recibido_mes (el primer día del mes UTC de la recepción), y la unicidad pasa a ser
-- (connection_id, delivery_id, recibido_mes): una entrega no puede repetirse DENTRO DEL MISMO
-- MES. Efecto: un reintento del proveedor que llegue en un mes distinto al de la primera
-- entrega (p. ej. entre el 31 y el 1) ya no se reconoce como duplicado. La API debe insertar
-- con ON CONFLICT DO NOTHING sin objetivo (un objetivo (connection_id, delivery_id) ya no
-- coincide con ninguna restricción); wi_mes_chk mantiene recibido_mes coherente con recibido_en.
-- La PK pasa a (recibido_mes, id) (017 la había dejado en (recibido_en, id)).
--
-- Se convierte como 033/035: tabla nueva, copia mes a mes conservando id, verificación
-- (filas, ids, huella) y retirada de la antigua, todo en una transacción. Durante la copia
-- cada tabla está bloqueada para escritura. Idempotente.
--
-- Requisito: pg_partman y el event trigger de db/00_extensiones.sql (ver 035).
-- Nota: el ticket nombra esta migración 026_particion_colas.sql, pero 026 ya existe.
-- ============================================================================

SET LOCAL lock_timeout = '30s';
SET LOCAL timezone = 'UTC';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman') THEN
        RAISE EXCEPTION
            '036: falta la extensión pg_partman. Créela con db/00_extensiones.sql (superusuario) sobre una imagen que la incluya (db/postgres/) o habilítela en el servidor (en Azure, azure.extensions) y vuelva a ejecutar.';
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'partman', 'USAGE, CREATE') THEN
        RAISE EXCEPTION
            '036: kallicode_admin no tiene permisos sobre el esquema partman. Vuelva a ejecutar db/00_extensiones.sql como superusuario.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'kc_particiones_sin_acceso' AND evtenabled <> 'D') THEN
        RAISE EXCEPTION
            '036: falta el event trigger kc_particiones_sin_acceso (las particiones no heredan la RLS del padre). Ejecute db/00_extensiones.sql como superusuario.';
    END IF;
END $$;

SET LOCAL ROLE kallicode_admin;

-- Barrido retroactivo: ninguna partición existente de core o vec debe ser accesible para la
-- aplicación ni el worker (no heredan la política RLS del padre). El event trigger lo hace con las
-- que se crean desde ahora; esto cubre las de bases donde se particionó antes de reforzarlo.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.oid::regclass::text AS tabla
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relispartition AND n.nspname IN ('core', 'vec')
           AND (has_table_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE, DELETE')
                OR has_table_privilege('kallicode_worker', c.oid, 'SELECT, INSERT, UPDATE, DELETE'))
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM kallicode_app, kallicode_worker', r.tabla);
        RAISE NOTICE '036: se retiró el acceso de la aplicación a la partición %', r.tabla;
    END LOOP;
END $$;

-- ============================================================================
-- 1. core.webhook_inbox
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
    r record;
BEGIN
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'core.webhook_inbox'::regclass) = 'p' THEN
        RAISE NOTICE '036: core.webhook_inbox ya está particionada; se omite.';
        RETURN;
    END IF;

    LOCK TABLE core.webhook_inbox IN ACCESS EXCLUSIVE MODE;

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || connection_id || delivery_id || coalesce(evento, '') || payload::text
                                 || estado || coalesce(error_detalle, '') || coalesce(ticket_id, '')
                                 || recibido_en::text || coalesce(procesado_en::text, ''))::numeric), 0),
           min(recibido_en)::date
      INTO antes, ids_antes, huella_antes, desde
      FROM core.webhook_inbox;

    ALTER TABLE core.webhook_inbox RENAME TO webhook_inbox_pre036;
    FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'core.webhook_inbox_pre036'::regclass LOOP
        EXECUTE format('ALTER TABLE core.webhook_inbox_pre036 RENAME CONSTRAINT %I TO %I', r.conname, r.conname || '_pre036');
    END LOOP;
    FOR r IN
        SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'core.webhook_inbox_pre036'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
    LOOP
        EXECUTE format('ALTER INDEX core.%I RENAME TO %I', r.relname, r.relname || '_pre036');
    END LOOP;

    CREATE TABLE core.webhook_inbox (
        id                bigint GENERATED ALWAYS AS IDENTITY,
        tenant_id         text NOT NULL,
        connection_id     text NOT NULL,
        delivery_id       text NOT NULL,
        evento            text,
        payload           jsonb NOT NULL,
        estado            text NOT NULL DEFAULT 'pendiente',
        error_detalle     text,
        ticket_id         text,
        recibido_en       timestamptz NOT NULL DEFAULT now(),
        procesado_en      timestamptz,
        -- Clave de partición: primer día del mes UTC de la recepción (ver la cabecera).
        recibido_mes      date NOT NULL DEFAULT date_trunc('month', now() AT TIME ZONE 'UTC')::date,
        CONSTRAINT webhook_inbox_pkey PRIMARY KEY (recibido_mes, id),
        CONSTRAINT wi_delivery_uq UNIQUE (connection_id, delivery_id, recibido_mes),
        CONSTRAINT wi_mes_chk CHECK (recibido_mes = date_trunc('month', recibido_en AT TIME ZONE 'UTC')::date),
        CONSTRAINT wi_estado_chk CHECK (estado IN ('pendiente', 'procesado', 'ignorado', 'fallido')),
        CONSTRAINT webhook_inbox_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id),
        CONSTRAINT webhook_inbox_connection_id_fkey FOREIGN KEY (tenant_id, connection_id)
            REFERENCES core.connections(tenant_id, id) ON DELETE CASCADE,
        CONSTRAINT webhook_inbox_ticket_id_fkey FOREIGN KEY (tenant_id, ticket_id)
            REFERENCES core.tickets(tenant_id, id)
    ) PARTITION BY RANGE (recibido_mes);

    CREATE INDEX idx_webhook_inbox_tenant_connection_id ON core.webhook_inbox (tenant_id, connection_id);
    CREATE INDEX idx_webhook_inbox_tenant_ticket_id     ON core.webhook_inbox (tenant_id, ticket_id);
    CREATE INDEX webhook_inbox_pend_ix                  ON core.webhook_inbox (estado, recibido_en)
        WHERE estado IN ('pendiente', 'fallido');
    CREATE INDEX webhook_inbox_tenant_ix                ON core.webhook_inbox (tenant_id, recibido_en DESC);

    ALTER TABLE core.webhook_inbox ENABLE ROW LEVEL SECURITY;
    ALTER TABLE core.webhook_inbox FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON core.webhook_inbox
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    -- 010_rol_worker.sql: el worker barre la cola de TODOS los tenants.
    CREATE POLICY worker_colas ON core.webhook_inbox TO kallicode_worker USING (true);
    REVOKE ALL ON core.webhook_inbox FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE, DELETE ON core.webhook_inbox TO kallicode_app;
    GRANT SELECT, UPDATE, DELETE ON core.webhook_inbox TO kallicode_worker;

    PERFORM partman.create_parent(
        p_parent_table    := 'core.webhook_inbox',
        p_control         := 'recibido_mes',
        p_interval        := '1 month',
        p_type            := 'range',
        p_premake         := 12,
        p_start_partition := to_char(date_trunc('month', coalesce(desde, current_date)::timestamp), 'YYYY-MM-DD'),
        p_default_table   := true,
        p_jobmon          := false);
    UPDATE partman.part_config
       SET retention = NULL, retention_keep_table = true, inherit_privileges = false,
           infinite_time_partitions = true
     WHERE parent_table = 'core.webhook_inbox';

    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', desde::timestamp),
                                   date_trunc('month', (SELECT max(recibido_en) FROM core.webhook_inbox_pre036)
                                                       AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO core.webhook_inbox
                   (id, tenant_id, connection_id, delivery_id, evento, payload, estado, error_detalle, ticket_id,
                    recibido_en, procesado_en, recibido_mes)
            OVERRIDING SYSTEM VALUE
            SELECT id, tenant_id, connection_id, delivery_id, evento, payload, estado, error_detalle, ticket_id,
                   recibido_en, procesado_en, date_trunc('month', recibido_en AT TIME ZONE 'UTC')::date
              FROM core.webhook_inbox_pre036
             WHERE recibido_en >= mes::timestamptz
               AND recibido_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;
    EXECUTE format('ALTER TABLE core.webhook_inbox ALTER COLUMN id RESTART WITH %s',
                   coalesce((SELECT max(id) FROM core.webhook_inbox_pre036), 0) + 1);

    SELECT count(*), coalesce(sum(id), 0),
           coalesce(sum(hashtext(tenant_id || connection_id || delivery_id || coalesce(evento, '') || payload::text
                                 || estado || coalesce(error_detalle, '') || coalesce(ticket_id, '')
                                 || recibido_en::text || coalesce(procesado_en::text, ''))::numeric), 0)
      INTO despues, ids_despues, huella_despues
      FROM core.webhook_inbox;
    IF despues <> antes OR ids_despues <> ids_antes OR huella_despues <> huella_antes THEN
        RAISE EXCEPTION '036: webhook_inbox: la copia no coincide (filas % -> %, ids % -> %, huella % -> %)',
            antes, despues, ids_antes, ids_despues, huella_antes, huella_despues;
    END IF;
    IF EXISTS (SELECT 1 FROM ONLY core.webhook_inbox_default) THEN
        RAISE EXCEPTION '036: webhook_inbox: hay filas en la partición DEFAULT; falta una partición para su fecha';
    END IF;

    DROP TABLE core.webhook_inbox_pre036;
    RAISE NOTICE '036: core.webhook_inbox particionada (% filas copiadas).', despues;
END $$;

-- ============================================================================
-- 2. core.notifications
-- ============================================================================
DO $$
DECLARE
    antes bigint;
    despues bigint;
    ids_antes numeric;
    ids_despues numeric;
    huella_antes numeric;
    huella_despues numeric;
    desde date;
    mes date;
    r record;
BEGIN
    IF (SELECT c.relkind FROM pg_class c WHERE c.oid = 'core.notifications'::regclass) = 'p' THEN
        RAISE NOTICE '036: core.notifications ya está particionada; se omite.';
        RETURN;
    END IF;

    LOCK TABLE core.notifications IN ACCESS EXCLUSIVE MODE;

    SELECT count(*), coalesce(sum(hashtext(id)::numeric), 0),
           coalesce(sum(hashtext(id || tenant_id || user_id || tipo || titulo || coalesce(cuerpo, '')
                                 || coalesce(ticket_id, '') || leida::text || creada_en::text)::numeric), 0),
           min(creada_en)::date
      INTO antes, ids_antes, huella_antes, desde
      FROM core.notifications;

    ALTER TABLE core.notifications RENAME TO notifications_pre036;
    FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'core.notifications_pre036'::regclass LOOP
        EXECUTE format('ALTER TABLE core.notifications_pre036 RENAME CONSTRAINT %I TO %I', r.conname, r.conname || '_pre036');
    END LOOP;
    FOR r IN
        SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'core.notifications_pre036'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
    LOOP
        EXECUTE format('ALTER INDEX core.%I RENAME TO %I', r.relname, r.relname || '_pre036');
    END LOOP;

    CREATE TABLE core.notifications (
        id                text NOT NULL,
        tenant_id         text NOT NULL,
        user_id           text NOT NULL,
        tipo              text NOT NULL,
        titulo            text NOT NULL,
        cuerpo            text,
        ticket_id         text,
        leida             boolean NOT NULL DEFAULT false,
        creada_en         timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT notifications_pkey PRIMARY KEY (creada_en, id),
        CONSTRAINT nt_tipo_chk CHECK (tipo IN ('cambio_etapa', 'gate_pendiente', 'ticket_cerrado',
                                               'aviso_cuota', 'seguridad', 'sistema')),
        CONSTRAINT notifications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.organizations(id),
        CONSTRAINT notifications_ticket_id_fkey FOREIGN KEY (tenant_id, ticket_id)
            REFERENCES core.tickets(tenant_id, id) ON DELETE CASCADE,
        CONSTRAINT notifications_user_id_fkey FOREIGN KEY (tenant_id, user_id)
            REFERENCES core.users(tenant_id, id) ON DELETE CASCADE
    ) PARTITION BY RANGE (creada_en);

    CREATE INDEX idx_notifications_tenant_ticket_id  ON core.notifications (tenant_id, ticket_id);
    CREATE INDEX idx_notifications_tenant_user_id    ON core.notifications (tenant_id, user_id);
    CREATE INDEX notifications_tenant_noleidas_ix    ON core.notifications (tenant_id, user_id) WHERE NOT leida;
    CREATE INDEX notifications_tenant_user_ix        ON core.notifications (tenant_id, user_id, creada_en DESC);

    ALTER TABLE core.notifications ENABLE ROW LEVEL SECURITY;
    ALTER TABLE core.notifications FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON core.notifications
        USING (tenant_id = core.current_tenant())
        WITH CHECK (tenant_id = core.current_tenant());
    REVOKE ALL ON core.notifications FROM PUBLIC;
    GRANT SELECT, INSERT, UPDATE, DELETE ON core.notifications TO kallicode_app;

    PERFORM partman.create_parent(
        p_parent_table    := 'core.notifications',
        p_control         := 'creada_en',
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
     WHERE parent_table = 'core.notifications';

    IF antes > 0 THEN
        FOR mes IN
            SELECT g::date
              FROM generate_series(date_trunc('month', desde::timestamp),
                                   date_trunc('month', (SELECT max(creada_en) FROM core.notifications_pre036)
                                                       AT TIME ZONE 'UTC'),
                                   interval '1 month') AS g
        LOOP
            INSERT INTO core.notifications
                   (id, tenant_id, user_id, tipo, titulo, cuerpo, ticket_id, leida, creada_en)
            SELECT id, tenant_id, user_id, tipo, titulo, cuerpo, ticket_id, leida, creada_en
              FROM core.notifications_pre036
             WHERE creada_en >= mes::timestamptz
               AND creada_en <  (mes + interval '1 month')::timestamptz;
        END LOOP;
    END IF;

    SELECT count(*), coalesce(sum(hashtext(id)::numeric), 0),
           coalesce(sum(hashtext(id || tenant_id || user_id || tipo || titulo || coalesce(cuerpo, '')
                                 || coalesce(ticket_id, '') || leida::text || creada_en::text)::numeric), 0)
      INTO despues, ids_despues, huella_despues
      FROM core.notifications;
    IF despues <> antes OR ids_despues <> ids_antes OR huella_despues <> huella_antes THEN
        RAISE EXCEPTION '036: notifications: la copia no coincide (filas % -> %, ids % -> %, huella % -> %)',
            antes, despues, ids_antes, ids_despues, huella_antes, huella_despues;
    END IF;
    IF EXISTS (SELECT 1 FROM ONLY core.notifications_default) THEN
        RAISE EXCEPTION '036: notifications: hay filas en la partición DEFAULT; falta una partición para su fecha';
    END IF;

    DROP TABLE core.notifications_pre036;
    RAISE NOTICE '036: core.notifications particionada (% filas copiadas).', despues;
END $$;

-- ============================================================================
-- 3. Retención por partición: configuración
--
-- La purga es de TABLA, no de tenant: una partición se elimina entera, así que no puede aplicarse
-- por tenant (core.retention_policies, 034, es por tenant y no gobierna esto). Los 90 días del
-- webhook_inbox son los del §9.3. Para notifications el ticket no fija plazo: se propone el mismo
-- (90 días); se cambia con un UPDATE de esta tabla, sin tocar código. Un NULL no existe: para
-- no purgar una tabla, se borra su fila.
-- ============================================================================
CREATE TABLE IF NOT EXISTS core.retencion_particiones (
    tabla       text PRIMARY KEY,
    retencion   interval NOT NULL,
    CONSTRAINT rp_retencion_chk CHECK (retencion >= interval '1 day')
);
COMMENT ON TABLE core.retencion_particiones IS
'KC-DB-07-3: plazo tras el cual core.purgar_particiones_vencidas() elimina (DROP) una partición. Global, no por tenant.';
REVOKE ALL ON core.retencion_particiones FROM PUBLIC, kallicode_app, kallicode_worker;
INSERT INTO core.retencion_particiones (tabla, retencion) VALUES
    ('core.webhook_inbox', interval '90 days'),
    ('core.notifications', interval '90 days')
ON CONFLICT (tabla) DO NOTHING;

-- ============================================================================
-- 4. Purga por partición
--
-- Elimina (DROP TABLE) las particiones cuyo límite superior ya cumplió el plazo: TODAS sus filas
-- son más antiguas que la retención. Nunca ejecuta DELETE. Salvaguardas:
--   * webhook_inbox: una partición con entregas 'pendiente' (sin procesar) NO se elimina; el
--     motivo queda en el resultado para que alguien las atienda. fallido/ignorado/procesado sí.
--   * Si no consigue el bloqueo en 5 s (consultas largas sobre la tabla), omite la partición y
--     lo intentará en la siguiente ejecución.
--   * La partición DEFAULT y las del mes en curso y futuras no cumplen nunca el plazo.
-- p_simular = true informa sin eliminar. Quien la ejecuta debe ser dueño de las particiones
-- (kallicode_admin: housekeeping la llama cada hora).
-- ============================================================================
CREATE OR REPLACE FUNCTION core.purgar_particiones_vencidas(p_ahora timestamptz DEFAULT now(),
                                                            p_simular boolean DEFAULT false)
RETURNS TABLE (tabla text, particion text, hasta timestamptz, filas bigint, accion text, motivo text)
LANGUAGE plpgsql
SET timezone = 'UTC' AS $$
DECLARE
    cfg record;
    part record;
    pendientes bigint;
BEGIN
    FOR cfg IN SELECT rp.tabla AS t, rp.retencion AS ret FROM core.retencion_particiones rp ORDER BY rp.tabla LOOP
        FOR part IN
            SELECT c.oid::regclass::text AS nombre,
                   (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'TO \(''([^'']+)''\)'))[1]::timestamptz AS limite
              FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
             WHERE i.inhparent = cfg.t::regclass
               AND pg_get_expr(c.relpartbound, c.oid) <> 'DEFAULT'
             ORDER BY 2
        LOOP
            CONTINUE WHEN part.limite IS NULL OR part.limite > p_ahora - cfg.ret;

            tabla := cfg.t; particion := part.nombre; hasta := part.limite;
            EXECUTE format('SELECT count(*) FROM %s', part.nombre) INTO filas;

            IF cfg.t = 'core.webhook_inbox' THEN
                EXECUTE format('SELECT count(*) FROM %s WHERE estado = ''pendiente''', part.nombre) INTO pendientes;
                IF pendientes > 0 THEN
                    accion := 'omitida';
                    motivo := format('tiene %s entregas pendientes de procesar', pendientes);
                    RETURN NEXT;
                    CONTINUE;
                END IF;
            END IF;

            IF p_simular THEN
                accion := 'simulada'; motivo := NULL;
                RETURN NEXT;
                CONTINUE;
            END IF;

            BEGIN
                SET LOCAL lock_timeout = '5s';
                EXECUTE format('DROP TABLE %s', part.nombre);
                accion := 'eliminada'; motivo := NULL;
            EXCEPTION WHEN lock_not_available THEN
                accion := 'omitida'; motivo := 'no se obtuvo el bloqueo en 5 s; se reintenta en la próxima ejecución';
            END;
            RETURN NEXT;
        END LOOP;
    END LOOP;
END $$;

COMMENT ON FUNCTION core.purgar_particiones_vencidas(timestamptz, boolean) IS
'KC-DB-07-3: elimina (DROP, nunca DELETE) las particiones cuyo plazo de core.retencion_particiones venció. No elimina particiones de webhook_inbox con entregas pendientes. Devuelve qué hizo con cada una.';
REVOKE ALL ON FUNCTION core.purgar_particiones_vencidas(timestamptz, boolean) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ============================================================================
-- 5. Comprobación final: la aplicación y el worker no tienen permisos sobre las particiones
-- ============================================================================
DO $$
DECLARE
    expuestas text;
BEGIN
    SELECT string_agg(c.oid::regclass::text, ', ') INTO expuestas
      FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
     WHERE i.inhparent IN ('core.webhook_inbox'::regclass, 'core.notifications'::regclass)
       AND (has_table_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE, DELETE')
            OR has_table_privilege('kallicode_worker', c.oid, 'SELECT, INSERT, UPDATE, DELETE'));
    IF expuestas IS NOT NULL THEN
        RAISE EXCEPTION '036: particiones con permisos para la aplicación o el worker (se saltarían la RLS del padre): %', expuestas;
    END IF;
END $$;

ANALYZE core.webhook_inbox;
ANALYZE core.notifications;
