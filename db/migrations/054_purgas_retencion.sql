-- ============================================================================
-- KALLICODE · 054_purgas_retencion.sql
-- KC-DB-10-1 (8 SP) — Purgas automáticas por pg_cron parametrizadas en core.retention_policies.
--
-- Hasta ahora los plazos de retención vivían en tres sitios: core.retention_policies (034, por tenant, sin
-- consumidor), core.retencion_particiones (036, 90 días fijos para webhook_inbox y notifications) y
-- core.retencion_declaraciones (053, las tablas que se conservan). Esta migración los unifica: los plazos viven
-- SOLO en core.retention_policies y un motor (core.purgar_retencion) los lee en cada ejecución, de modo que
-- cambiar los días de una fila cambia el comportamiento de la siguiente ejecución sin tocar SQL.
--
--   1. core.retention_policies admite filas GLOBALES (tenant_id NULL = política de plataforma) además de las
--      por tenant, y la acción 'conservar' (no se purga: es la declaración que exige el chequeo V26). Columnas
--      nuevas: columna_tiempo (tablas no particionadas), lote (filas por lote), motivo.
--   2. core.retention_purgas: registro de cada acción del motor (qué, cuándo, cuántas filas).
--   3. core.puede_descartar_particion(): gancho previo al descarte de una partición. Hoy bloquea las particiones
--      de webhook_inbox con entregas pendientes y las de audit.audit_events (sin comprobante de export WORM;
--      KC-DB-10-2 sustituye este gancho por la comprobación real).
--   4. core.purgar_retencion(): el motor. Dos modos según la tabla:
--        * partición  (tablas particionadas por rango de tiempo con política global): DROP, DETACH o
--          DETACH + archivo de las particiones cuyo límite superior superó el plazo. Nunca DELETE.
--        * filas      (tablas no particionadas con columna_tiempo, y políticas por tenant): borrado por lotes
--          acotados con FOR UPDATE SKIP LOCKED: no espera a las filas bloqueadas ni retiene bloqueos largos.
--      Una política por tenant solo puede ACORTAR el plazo de la de plataforma y solo existe para entidades que
--      la plataforma ya gobierna (el nombre de la tabla nunca sale de lo que escribe un tenant).
--   5. core.purgar_particiones_vencidas() (036) pasa a delegar en el motor con las mismas columnas de salida:
--      housekeeping y las pruebas de la 036 siguen funcionando.
--   6. pg_cron: trabajo diario 'retencion_purgas' (una sola sentencia).
--   7. V26 (core.contar_tablas_sin_retencion) lee core.retention_policies.
--
-- Nota: el ticket no nombra archivos. La 053 la comparten dos migraciones (catálogo de precios y V22-V26); esta es la
-- siguiente libre, la 054.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ---------------------------------------------------------------------------
-- 1. core.retention_policies: filas globales, 'conservar', columna_tiempo, lote, motivo
-- ---------------------------------------------------------------------------
ALTER TABLE core.retention_policies DROP CONSTRAINT IF EXISTS retention_policies_pkey;
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_pkey PRIMARY KEY (id);
ALTER TABLE core.retention_policies ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE core.retention_policies ALTER COLUMN retention_days DROP NOT NULL;

-- Una política por (tenant, entidad) y una global por entidad.
CREATE UNIQUE INDEX IF NOT EXISTS retention_policies_tenant_entidad_uq
    ON core.retention_policies (tenant_id, entity_name) WHERE tenant_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS retention_policies_global_entidad_uq
    ON core.retention_policies (entity_name) WHERE tenant_id IS NULL;

ALTER TABLE core.retention_policies ADD COLUMN IF NOT EXISTS columna_tiempo text;
ALTER TABLE core.retention_policies ADD COLUMN IF NOT EXISTS lote integer NOT NULL DEFAULT 5000;
ALTER TABLE core.retention_policies ADD COLUMN IF NOT EXISTS motivo text;

ALTER TABLE core.retention_policies DROP CONSTRAINT IF EXISTS retention_policies_action_chk;
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_action_chk
    CHECK (action IN ('detach', 'drop', 'archive', 'conservar'));
ALTER TABLE core.retention_policies DROP CONSTRAINT IF EXISTS retention_policies_days_chk;
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_days_chk
    CHECK (retention_days IS NULL OR retention_days > 0);
-- 'conservar' no lleva plazo; las demás acciones sí.
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_dias_accion_chk
    CHECK ((action = 'conservar') = (retention_days IS NULL));
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_entidad_chk
    CHECK (entity_name ~ '^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$');
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_columna_chk
    CHECK (columna_tiempo IS NULL OR columna_tiempo ~ '^[a-z_][a-z0-9_]*$');
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_lote_chk
    CHECK (lote BETWEEN 100 AND 100000);
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_motivo_len_chk
    CHECK (motivo IS NULL OR char_length(motivo) <= 20000);
-- Las políticas por tenant solo borran filas: no tienen sentido como 'conservar', detach ni archive.
ALTER TABLE core.retention_policies ADD CONSTRAINT retention_policies_tenant_accion_chk
    CHECK (tenant_id IS NULL OR action = 'drop');

COMMENT ON TABLE core.retention_policies IS
'KC-DB-07-4 / KC-DB-10-1: ÚNICA fuente de los plazos de retención. tenant_id NULL = política de plataforma (la que gobierna las particiones y las tablas no particionadas); con tenant_id, una política que solo puede ACORTAR el plazo de la de plataforma para ese tenant (borrado de sus filas). action: drop | detach | archive | conservar (declara que no se purga). Las lee core.purgar_retencion().';
COMMENT ON COLUMN core.retention_policies.columna_tiempo IS
'Columna con la fecha de cada fila, necesaria para tablas NO particionadas (en las particionadas se toma de la clave de partición).';
COMMENT ON COLUMN core.retention_policies.lote IS
'Filas por lote en el borrado por filas (100-100.000): acota la duración de cada transacción de borrado.';

-- ---------------------------------------------------------------------------
-- 2. Registro de las acciones del motor
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS retencion_archivo;
REVOKE ALL ON SCHEMA retencion_archivo FROM PUBLIC, kallicode_app, kallicode_worker;
COMMENT ON SCHEMA retencion_archivo IS
'KC-DB-10-1: particiones desacopladas con action = archive (conservadas fuera de la tabla activa, sin acceso de la aplicación).';

CREATE TABLE IF NOT EXISTS core.retention_purgas (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ejecutado_en  timestamptz NOT NULL DEFAULT now(),
    entidad       text NOT NULL,
    tenant_id     text,                       -- NULL = política de plataforma
    objeto        text,                       -- partición o tabla sobre la que actuó
    accion        text NOT NULL,
    filas         bigint,
    motivo        text,
    CONSTRAINT retention_purgas_filas_chk CHECK (filas IS NULL OR filas >= 0),
    CONSTRAINT retention_purgas_motivo_len_chk CHECK (motivo IS NULL OR char_length(motivo) <= 20000)
);
-- Sin índice propio: unas pocas filas por entidad y día; la consulta del último descarte la recorre sin coste.
COMMENT ON TABLE core.retention_purgas IS
'KC-DB-10-1: lo que hizo cada ejecución real de core.purgar_retencion() (eliminada, desacoplada, archivada, borradas, omitida, bloqueada). Base del informe de cumplimiento (KC-DB-10-4).';
REVOKE ALL ON core.retention_purgas FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 3. Unificar los plazos y las declaraciones anteriores en core.retention_policies
-- ---------------------------------------------------------------------------
INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo)
SELECT NULL, rp.tabla, ceil(extract(epoch FROM rp.retencion) / 86400)::int, 'drop',
       'Plazo migrado de core.retencion_particiones (036).'
  FROM core.retencion_particiones rp
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;

INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo)
SELECT NULL, d.tabla, NULL, 'conservar', d.politica || ': ' || d.justificacion
  FROM core.retencion_declaraciones d
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;

DROP TABLE core.retencion_particiones;
DROP TABLE core.retencion_declaraciones;

-- ---------------------------------------------------------------------------
-- 4. Gancho previo al descarte de una partición
--    KC-DB-10-2 sustituye la rama de audit.audit_events por la comprobación del comprobante WORM.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.puede_descartar_particion(p_tabla text, p_particion regclass)
RETURNS TABLE (permitido boolean, motivo text)
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    pendientes bigint;
BEGIN
    permitido := true; motivo := NULL;
    IF p_tabla = 'core.webhook_inbox' THEN
        EXECUTE format('SELECT count(*) FROM %s WHERE estado = ''pendiente''', p_particion) INTO pendientes;
        IF pendientes > 0 THEN
            permitido := false;
            motivo := format('tiene %s entregas pendientes de procesar', pendientes);
        END IF;
    ELSIF p_tabla = 'audit.audit_events' THEN
        permitido := false;
        motivo := 'sin comprobante de export WORM verificado (KC-DB-10-2)';
    END IF;
    RETURN NEXT;
END $$;
COMMENT ON FUNCTION core.puede_descartar_particion(text, regclass) IS
'KC-DB-10-1: gancho previo al descarte de una partición; devuelve (permitido, motivo). Bloquea webhook_inbox con entregas pendientes y audit.audit_events sin comprobante WORM (KC-DB-10-2).';
REVOKE ALL ON FUNCTION core.puede_descartar_particion(text, regclass) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 5. Borrado por lotes de una hoja (partición o tabla): SKIP LOCKED, sin esperar a filas bloqueadas
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.borrar_lote_retencion(p_hoja regclass, p_columna text, p_corte timestamptz,
                                                      p_tenant text, p_lote integer)
RETURNS bigint
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    n bigint;
BEGIN
    EXECUTE format(
        'WITH v AS (SELECT ctid AS c FROM %1$s WHERE %2$I < $1 AND ($2 IS NULL OR tenant_id = $2) '
        '           LIMIT $3 FOR UPDATE SKIP LOCKED) '
        'DELETE FROM %1$s t USING v WHERE t.ctid = v.c',
        p_hoja, p_columna)
      USING p_corte, p_tenant, p_lote;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;
COMMENT ON FUNCTION core.borrar_lote_retencion(regclass, text, timestamptz, text, integer) IS
'KC-DB-10-1: borra hasta p_lote filas anteriores a p_corte de una hoja (de un tenant si se indica), saltando las filas bloqueadas por otras transacciones. Devuelve las borradas.';
REVOKE ALL ON FUNCTION core.borrar_lote_retencion(regclass, text, timestamptz, text, integer)
    FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 6. El motor
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.purgar_retencion(p_ahora timestamptz DEFAULT now(),
                                                 p_simular boolean DEFAULT false,
                                                 p_solo text DEFAULT NULL,
                                                 p_max_lotes integer DEFAULT 100)
RETURNS TABLE (entidad text, tenant text, objeto text, hasta timestamptz, filas bigint, accion text, motivo text)
LANGUAGE plpgsql
SET timezone = 'UTC' AS $$
DECLARE
    pol record;
    part record;
    leaf record;
    reg regclass;
    es_part boolean;
    col text;
    corte timestamptz;
    lotes integer;
    n bigint;
    total bigint;
    perm boolean;
    mot text;
BEGIN
    IF p_solo IS NOT NULL AND p_solo NOT IN ('particion', 'filas') THEN
        RAISE EXCEPTION 'core.purgar_retencion: p_solo debe ser particion, filas o NULL (recibido %)', p_solo;
    END IF;

    -- ---- Políticas de plataforma -------------------------------------------------------------------
    FOR pol IN SELECT * FROM core.retention_policies p
                WHERE p.tenant_id IS NULL AND p.is_active AND p.action <> 'conservar'
                ORDER BY p.entity_name LOOP
        entidad := pol.entity_name; tenant := NULL; objeto := NULL; hasta := NULL; filas := NULL; motivo := NULL;
        reg := to_regclass(pol.entity_name);
        IF reg IS NULL THEN
            accion := 'omitida'; motivo := 'la entidad no existe';
            RETURN NEXT;
            CONTINUE;
        END IF;
        corte := p_ahora - make_interval(days => pol.retention_days);
        es_part := EXISTS (SELECT 1 FROM pg_partitioned_table pt WHERE pt.partrelid = reg AND pt.partstrat = 'r');

        IF es_part THEN
            IF p_solo IS NULL OR p_solo = 'particion' THEN
                FOR part IN
                    SELECT c.oid::regclass AS nombre,
                           (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'TO \(''([^'']+)''\)'))[1]::timestamptz AS limite
                      FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                     WHERE i.inhparent = reg AND pg_get_expr(c.relpartbound, c.oid) <> 'DEFAULT'
                     ORDER BY 2
                LOOP
                    CONTINUE WHEN part.limite IS NULL OR part.limite > corte;
                    objeto := part.nombre::text; hasta := part.limite;
                    EXECUTE format('SELECT count(*) FROM %s', part.nombre) INTO filas;

                    SELECT g.permitido, g.motivo INTO perm, mot FROM core.puede_descartar_particion(pol.entity_name, part.nombre) g;
                    IF NOT perm THEN
                        accion := 'omitida'; motivo := mot;
                    ELSIF p_simular THEN
                        accion := 'simulada'; motivo := NULL;
                    ELSE
                        BEGIN
                            SET LOCAL lock_timeout = '5s';
                            IF pol.action = 'drop' THEN
                                EXECUTE format('DROP TABLE %s', part.nombre);
                                accion := 'eliminada';
                            ELSE
                                EXECUTE format('ALTER TABLE %s DETACH PARTITION %s', reg, part.nombre);
                                accion := 'desacoplada';
                                IF pol.action = 'archive' THEN
                                    EXECUTE format('ALTER TABLE %s SET SCHEMA retencion_archivo', part.nombre);
                                    accion := 'archivada';
                                    objeto := part.nombre::text;   -- ya en retencion_archivo
                                END IF;
                            END IF;
                            motivo := NULL;
                        EXCEPTION WHEN lock_not_available THEN
                            accion := 'omitida';
                            motivo := 'no se obtuvo el bloqueo en 5 s; se reintenta en la próxima ejecución';
                        END;
                    END IF;
                    IF NOT p_simular THEN
                        INSERT INTO core.retention_purgas (entidad, tenant_id, objeto, accion, filas, motivo)
                        VALUES (entidad, NULL, objeto, accion, filas, motivo);
                    END IF;
                    RETURN NEXT;
                END LOOP;
            END IF;
        ELSIF p_solo IS NULL OR p_solo = 'filas' THEN
            -- tabla no particionada: borrado por lotes de las filas anteriores al plazo
            col := pol.columna_tiempo;
            IF col IS NULL OR NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = reg AND a.attname = col AND NOT a.attisdropped) THEN
                accion := 'omitida'; motivo := 'la política no indica una columna_tiempo válida';
                RETURN NEXT;
                CONTINUE;
            END IF;
            objeto := reg::text; hasta := corte; total := 0;
            IF p_simular THEN
                EXECUTE format('SELECT count(*) FROM %s WHERE %I < $1', reg, col) INTO filas USING corte;
                CONTINUE WHEN filas = 0;
                accion := 'simulada'; motivo := NULL;
            ELSE
                lotes := 0;
                LOOP
                    n := core.borrar_lote_retencion(reg, col, corte, NULL, pol.lote);
                    total := total + n; lotes := lotes + 1;
                    EXIT WHEN n < pol.lote OR lotes >= p_max_lotes;
                END LOOP;
                CONTINUE WHEN total = 0;   -- nada vencido: ni se informa ni se registra
                filas := total; accion := 'borradas';
                motivo := CASE WHEN lotes >= p_max_lotes AND n >= pol.lote
                               THEN format('quedan filas vencidas: máximo de %s lotes por ejecución', p_max_lotes) END;
                INSERT INTO core.retention_purgas (entidad, tenant_id, objeto, accion, filas, motivo)
                VALUES (entidad, NULL, objeto, accion, filas, motivo);
            END IF;
            RETURN NEXT;
        END IF;
    END LOOP;

    -- ---- Políticas por tenant: solo acortan el plazo de plataforma y solo para entidades que ella gobierna ----
    IF p_solo IS NULL OR p_solo = 'filas' THEN
        FOR pol IN
            SELECT t.entity_name, t.tenant_id, t.retention_days, t.lote, g.columna_tiempo AS gcol,
                   g.retention_days AS dias_plataforma
              FROM core.retention_policies t
              JOIN core.retention_policies g
                ON g.tenant_id IS NULL AND g.entity_name = t.entity_name AND g.is_active AND g.action <> 'conservar'
             WHERE t.tenant_id IS NOT NULL AND t.is_active
             ORDER BY t.entity_name, t.tenant_id
        LOOP
            entidad := pol.entity_name; tenant := pol.tenant_id; objeto := NULL; hasta := NULL; filas := NULL; motivo := NULL;
            reg := to_regclass(pol.entity_name);
            IF reg IS NULL THEN CONTINUE; END IF;
            IF pol.retention_days >= pol.dias_plataforma THEN
                accion := 'ignorada';
                motivo := 'una política por tenant no puede alargar el plazo de la de plataforma';
                RETURN NEXT;
                CONTINUE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = reg AND a.attname = 'tenant_id' AND NOT a.attisdropped) THEN
                accion := 'ignorada'; motivo := 'la entidad no tiene tenant_id';
                RETURN NEXT;
                CONTINUE;
            END IF;
            es_part := EXISTS (SELECT 1 FROM pg_partitioned_table pt WHERE pt.partrelid = reg AND pt.partstrat = 'r');
            col := pol.gcol;
            IF es_part THEN
                col := (regexp_match(pg_get_partkeydef(reg), '^RANGE \(([a-z_][a-z0-9_]*)\)$'))[1];
            END IF;
            IF col IS NULL THEN
                accion := 'ignorada'; motivo := 'no se conoce la columna de fecha de la entidad';
                RETURN NEXT;
                CONTINUE;
            END IF;
            corte := p_ahora - make_interval(days => pol.retention_days);
            hasta := corte; total := 0;
            FOR leaf IN SELECT h AS hoja FROM (
                            SELECT relid::oid AS h FROM pg_partition_tree(reg) WHERE isleaf
                            UNION
                            SELECT reg::oid WHERE (SELECT relkind FROM pg_class WHERE oid = reg) = 'r') x
                         ORDER BY h::regclass::text
            LOOP
                objeto := leaf.hoja::regclass::text;
                IF p_simular THEN
                    EXECUTE format('SELECT count(*) FROM %s WHERE %I < $1 AND tenant_id = $2', leaf.hoja::regclass, col)
                      INTO filas USING corte, pol.tenant_id;
                    CONTINUE WHEN filas = 0;
                    accion := 'simulada'; motivo := NULL;
                    RETURN NEXT;
                ELSE
                    lotes := 0; total := 0;
                    LOOP
                        n := core.borrar_lote_retencion(leaf.hoja::regclass, col, corte, pol.tenant_id, pol.lote);
                        total := total + n; lotes := lotes + 1;
                        EXIT WHEN n < pol.lote OR lotes >= p_max_lotes;
                    END LOOP;
                    CONTINUE WHEN total = 0;
                    filas := total; accion := 'borradas';
                    motivo := CASE WHEN lotes >= p_max_lotes AND n >= pol.lote
                                   THEN format('quedan filas vencidas: máximo de %s lotes por ejecución', p_max_lotes) END;
                    INSERT INTO core.retention_purgas (entidad, tenant_id, objeto, accion, filas, motivo)
                    VALUES (entidad, tenant, objeto, accion, filas, motivo);
                    RETURN NEXT;
                END IF;
            END LOOP;
        END LOOP;
    END IF;
END $$;
COMMENT ON FUNCTION core.purgar_retencion(timestamptz, boolean, text, integer) IS
'KC-DB-10-1: motor de purga gobernado por core.retention_policies (ningún plazo está escrito aquí). Particiones: DROP/DETACH/archivo de las vencidas, previa consulta de core.puede_descartar_particion. Filas: borrado por lotes con SKIP LOCKED. Políticas por tenant: solo acortan el plazo de plataforma. p_simular = true informa sin tocar; p_solo = particion | filas limita el modo. Registra en core.retention_purgas.';
REVOKE ALL ON FUNCTION core.purgar_retencion(timestamptz, boolean, text, integer) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 7. La purga de particiones de la 036 delega en el motor (mismas columnas de salida)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.purgar_particiones_vencidas(p_ahora timestamptz DEFAULT now(),
                                                            p_simular boolean DEFAULT false)
RETURNS TABLE (tabla text, particion text, hasta timestamptz, filas bigint, accion text, motivo text)
LANGUAGE sql AS $$
    SELECT e.entidad, e.objeto, e.hasta, e.filas, e.accion, e.motivo
      FROM core.purgar_retencion(p_ahora, p_simular, 'particion') e
$$;
COMMENT ON FUNCTION core.purgar_particiones_vencidas(timestamptz, boolean) IS
'KC-DB-07-3 / KC-DB-10-1: elimina (o desacopla) las particiones vencidas según core.retention_policies; delega en core.purgar_retencion(modo particion). No elimina particiones de webhook_inbox con entregas pendientes.';
REVOKE ALL ON FUNCTION core.purgar_particiones_vencidas(timestamptz, boolean) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 8. V26 lee core.retention_policies
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.contar_tablas_sin_retencion() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT count(*)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind = 'p'
       AND n.nspname IN ('core', 'audit', 'vec', 'cm')
       AND NOT EXISTS (SELECT 1 FROM core.retention_policies p
                        WHERE p.tenant_id IS NULL AND p.is_active
                          AND p.entity_name = n.nspname || '.' || c.relname)
$f$;
COMMENT ON FUNCTION core.contar_tablas_sin_retencion() IS
'KC-DB-09-6 / KC-DB-10-1: número de tablas particionadas (core, audit, vec, cm) sin política de plataforma activa en core.retention_policies (con acción conservar si no se purgan). La usa V26 de core.validar_integridad().';

-- ---------------------------------------------------------------------------
-- 9. pg_cron: purga diaria (una sola sentencia, como exige pg_cron)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regnamespace('cron') IS NULL THEN
        RAISE NOTICE '054: pg_cron no está instalada: no se programa la purga de retención (ejecute core.purgar_retencion() desde un planificador).';
        RETURN;
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'cron', 'USAGE') THEN
        RAISE NOTICE '054: kallicode_admin no tiene USAGE sobre el esquema cron: programe el trabajo retencion_purgas con un superusuario (docs/retencion_purgas.md).';
        RETURN;
    END IF;
    PERFORM cron.schedule('retencion_purgas', '20 3 * * *', $c$SELECT * FROM core.purgar_retencion()$c$);
END $$;
