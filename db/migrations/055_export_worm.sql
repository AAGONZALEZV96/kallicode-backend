-- ============================================================================
-- KALLICODE · 055_export_worm.sql
-- KC-DB-10-2 (5 SP) — Export WORM con hash encadenado previo al DROP de particiones de auditoría.
--
-- Una partición de audit.audit_events solo puede descartarse si existe un COMPROBANTE de export WORM verificado
-- de su contenido actual. El export (archivo JSON Lines + manifiesto) lo hace y lo verifica, sin base de datos,
-- db/tools/worm_auditoria.py; esta migración pone en la base lo que necesita ese procedimiento:
--
--   1. audit.worm_exports        comprobantes de export (append-only: sin UPDATE, DELETE ni TRUNCATE). Cada uno
--                                guarda el hash del archivo, el del manifiesto, el digest del contenido de la
--                                partición y el hash del export anterior: los exports forman una cadena.
--   2. audit.chain_anchors       «anclas» de cadena: por tenant, cuántos eventos se descartaron y el último sello
--                                descartado. Sin ellas, tras el DROP la cadena de cada tenant empezaría en un
--                                eslabón inexistente y audit.verify_chain, V16 y V17 la darían por rota.
--   3. audit.digest_particion / audit.manifiesto_particion   huella del contenido y resumen por tenant (primer y
--                                último sello, eventos, si la partición es un PREFIJO de la cadena del tenant y
--                                si audit.verify_chain la da por íntegra).
--   4. audit.registrar_export_worm   valida y registra el comprobante: la partición no ha cambiado desde el export,
--                                las cadenas están íntegras y el enlace con el export anterior es correcto.
--   5. audit.comprobante_worm_vigente / audit.anclar_descarte    la comprobación previa al DROP y el anclaje.
--   6. core.puede_descartar_particion   la rama de auditoría usa el comprobante (antes bloqueaba siempre, 054).
--   7. core.purgar_retencion     ancla la cadena antes de descartar una partición de auditoría.
--   8. audit.verify_chain y V17 (core.validar_integridad)   descuentan lo anclado: tras un descarte siguen en
--                                «íntegra» / 0 problemas.
--   9. audit.v_particiones_worm  qué particiones de auditoría hay y cuál es su último comprobante.
--
-- Solo se pueden descartar particiones EN ORDEN (las más antiguas primero): cada partición debe ser un prefijo de
-- la cadena de cada tenant. Sin política que lo ordene a purgar, nada cambia: la política de audit.audit_events
-- sigue siendo 'conservar' (decisión de negocio: cuánto tiempo se conserva la auditoría); ver docs/export_worm_auditoria.md.
--
-- Nota: el ticket no nombra archivos. La 054 la comparten dos migraciones; esta es la siguiente libre, la 055.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ---------------------------------------------------------------------------
-- 1. Comprobantes de export (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit.worm_exports (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    particion                text NOT NULL,          -- esquema.tabla de la partición exportada
    desde                    timestamptz NOT NULL,   -- límites de la partición
    hasta                    timestamptz NOT NULL,
    filas                    bigint NOT NULL,
    contenido_digest         text NOT NULL,          -- sha256 de las líneas «id:sello» ordenadas por id
    archivo_sha256           text NOT NULL,          -- sha256 del archivo exportado
    manifiesto_sha256        text NOT NULL,          -- sha256 del manifiesto
    export_anterior_sha256   text NOT NULL,          -- archivo_sha256 del export anterior (ceros si es el primero)
    archivo_ref              text NOT NULL,          -- dónde quedó el archivo (ruta o URI)
    tenants                  jsonb NOT NULL,         -- manifiesto por tenant (ver audit.manifiesto_particion)
    verificado_por           text NOT NULL,
    verificado_en            timestamptz NOT NULL DEFAULT now(),
    creado_en                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT we_filas_chk       CHECK (filas >= 0),
    CONSTRAINT we_limites_chk     CHECK (desde < hasta),
    CONSTRAINT we_digest_chk      CHECK (contenido_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT we_archivo_chk     CHECK (archivo_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT we_manifiesto_chk  CHECK (manifiesto_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT we_anterior_chk    CHECK (export_anterior_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT we_ref_len_chk     CHECK (char_length(archivo_ref) BETWEEN 1 AND 2000),
    CONSTRAINT we_verificado_chk  CHECK (length(btrim(verificado_por)) > 0),
    CONSTRAINT we_archivo_uq      UNIQUE (archivo_sha256)
);
-- Sin índice por partición: un comprobante por mes de auditoría; las búsquedas recorren decenas de filas.
COMMENT ON TABLE audit.worm_exports IS
'KC-DB-10-2: comprobantes de export WORM de particiones de audit.audit_events (append-only). Sin un comprobante verificado y vigente, core.purgar_retencion no descarta la partición.';
REVOKE ALL ON audit.worm_exports FROM PUBLIC, kallicode_app, kallicode_worker;

CREATE OR REPLACE FUNCTION audit.worm_exports_inmutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'audit.worm_exports es append-only (WORM): % prohibido', TG_OP USING ERRCODE = 'raise_exception';
END $$;
DROP TRIGGER IF EXISTS trg_worm_exports_inmutable ON audit.worm_exports;
CREATE TRIGGER trg_worm_exports_inmutable BEFORE UPDATE OR DELETE ON audit.worm_exports
    FOR EACH ROW EXECUTE FUNCTION audit.worm_exports_inmutable();
DROP TRIGGER IF EXISTS trg_worm_exports_no_truncate ON audit.worm_exports;
CREATE TRIGGER trg_worm_exports_no_truncate BEFORE TRUNCATE ON audit.worm_exports
    FOR EACH STATEMENT EXECUTE FUNCTION audit.worm_exports_inmutable();

-- ---------------------------------------------------------------------------
-- 2. Anclas de cadena
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit.chain_anchors (
    tenant_id                text PRIMARY KEY,   -- sin clave foránea: sobrevive a la supresión física del tenant
    eventos_descartados      bigint NOT NULL,
    ultimo_sello_descartado  text NOT NULL,
    ultimo_export_id         bigint,
    actualizado_en           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ca_eventos_pos_chk CHECK (eventos_descartados > 0),
    CONSTRAINT ca_sello_chk       CHECK (ultimo_sello_descartado ~ '^[0-9a-f]{64}$')
);
COMMENT ON TABLE audit.chain_anchors IS
'KC-DB-10-2: por tenant, cuántos eventos de su cadena se descartaron tras un export WORM y el último sello descartado. audit.verify_chain y V17 los descuentan. Solo avanza (escribe audit.anclar_descarte).';
ALTER TABLE audit.chain_anchors ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.chain_anchors FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON audit.chain_anchors;
CREATE POLICY tenant_isolation ON audit.chain_anchors FOR ALL
    USING (tenant_id = core.current_tenant()) WITH CHECK (tenant_id = core.current_tenant());
REVOKE ALL ON audit.chain_anchors FROM PUBLIC, kallicode_app, kallicode_worker;
GRANT SELECT ON audit.chain_anchors TO kallicode_app;   -- como audit.chain_heads: verify_chain la lee

CREATE OR REPLACE FUNCTION audit.chain_anchors_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit.chain_anchors: DELETE prohibido (las anclas solo avanzan)' USING ERRCODE = 'raise_exception';
    END IF;
    IF NEW.tenant_id <> OLD.tenant_id OR NEW.eventos_descartados <= OLD.eventos_descartados THEN
        RAISE EXCEPTION 'audit.chain_anchors: un ancla solo puede avanzar (más eventos descartados)' USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_chain_anchors_guard ON audit.chain_anchors;
CREATE TRIGGER trg_chain_anchors_guard BEFORE UPDATE OR DELETE ON audit.chain_anchors
    FOR EACH ROW EXECUTE FUNCTION audit.chain_anchors_guard();
CREATE OR REPLACE FUNCTION audit.chain_anchors_no_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'audit.chain_anchors: TRUNCATE prohibido' USING ERRCODE = 'raise_exception';
END $$;
DROP TRIGGER IF EXISTS trg_chain_anchors_no_truncate ON audit.chain_anchors;
CREATE TRIGGER trg_chain_anchors_no_truncate BEFORE TRUNCATE ON audit.chain_anchors
    FOR EACH STATEMENT EXECUTE FUNCTION audit.chain_anchors_no_truncate();

-- ---------------------------------------------------------------------------
-- 3. Límites, digest y manifiesto de una partición
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.limites_particion(p_particion regclass)
RETURNS TABLE (desde timestamptz, hasta timestamptz)
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp SET timezone = 'UTC' AS $$
    SELECT (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'FROM \(''([^'']+)''\)'))[1]::timestamptz,
           (regexp_match(pg_get_expr(c.relpartbound, c.oid), 'TO \(''([^'']+)''\)'))[1]::timestamptz
      FROM pg_class c
      JOIN pg_inherits i ON i.inhrelid = c.oid AND i.inhparent = 'audit.audit_events'::regclass
     WHERE c.oid = p_particion
$$;
COMMENT ON FUNCTION audit.limites_particion(regclass) IS
'KC-DB-10-2: límites [desde, hasta) de una partición por rango de audit.audit_events (sin filas para la DEFAULT o para una tabla que no es su partición).';

CREATE OR REPLACE FUNCTION audit.digest_particion(p_particion regclass) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    t text;
BEGIN
    EXECUTE format('SELECT coalesce(string_agg(id::text || '':'' || sello, E''\n'' ORDER BY id), '''') FROM %s', p_particion)
       INTO t;
    RETURN encode(sha256(convert_to(t, 'UTF8')), 'hex');
END $$;
COMMENT ON FUNCTION audit.digest_particion(regclass) IS
'KC-DB-10-2: sha256 de las líneas «id:sello» de la partición ordenadas por id (unidas por salto de línea). Cada sello compromete el contenido de su evento, así que el digest cambia si cambia cualquier evento o falta alguno.';

CREATE OR REPLACE FUNCTION audit.manifiesto_particion(p_particion regclass) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    genesis constant text := repeat('0', 64);
    t record;
    r jsonb := '[]'::jsonb;
    n bigint;
    inicios text[];
    finales text[];
    enlaces text[];
    ancla text;
    prefijo boolean;
    integra boolean;
BEGIN
    FOR t IN EXECUTE format('SELECT DISTINCT tenant_id FROM %s ORDER BY 1', p_particion) LOOP
        EXECUTE format(
            'WITH d AS (SELECT sello, sello_previo FROM %s WHERE tenant_id = $1), '
            '     o AS (SELECT sello_previo FROM audit.audit_events e WHERE e.tenant_id = $1 AND e.tableoid <> $2) '
            'SELECT (SELECT count(*) FROM d), '
            '       (SELECT array_agg(sello_previo) FROM d WHERE sello_previo NOT IN (SELECT sello FROM d)), '
            '       (SELECT array_agg(sello) FROM d WHERE sello NOT IN (SELECT sello_previo FROM d)), '
            '       (SELECT array_agg(sello_previo) FROM o WHERE sello_previo IN (SELECT sello FROM d))',
            p_particion)
          INTO n, inicios, finales, enlaces USING t.tenant_id, p_particion::oid;
        SELECT a.ultimo_sello_descartado INTO ancla FROM audit.chain_anchors a WHERE a.tenant_id = t.tenant_id;
        -- Es un prefijo de la cadena si tiene un solo comienzo y un solo final, el comienzo cuelga del ancla (o del
        -- génesis) y, si queda cadena fuera de la partición, esta continúa exactamente desde su final.
        prefijo := coalesce(cardinality(inicios), 0) = 1 AND coalesce(cardinality(finales), 0) = 1
                   AND inicios[1] = coalesce(ancla, genesis)
                   AND (enlaces IS NULL OR (cardinality(enlaces) = 1 AND enlaces[1] = finales[1]));
        SELECT v.integra INTO integra FROM audit.verify_chain(t.tenant_id) v;
        r := r || jsonb_build_array(jsonb_build_object(
            'tenant_id', t.tenant_id,
            'eventos', n,
            'primer_sello_previo', inicios[1],
            'ultimo_sello', finales[1],
            'prefijo', prefijo,
            'integra', integra));
    END LOOP;
    RETURN r;
END $$;
COMMENT ON FUNCTION audit.manifiesto_particion(regclass) IS
'KC-DB-10-2: por tenant de la partición: eventos, primer sello_previo, último sello, si la partición es un prefijo de su cadena (se puede descartar en orden) y si audit.verify_chain la da por íntegra.';

-- ---------------------------------------------------------------------------
-- 4. Registro del comprobante
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.registrar_export_worm(p_particion regclass, p_contenido_digest text,
                                                       p_archivo_sha256 text, p_manifiesto_sha256 text,
                                                       p_export_anterior_sha256 text, p_archivo_ref text,
                                                       p_tenants jsonb, p_verificado_por text)
RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    genesis constant text := repeat('0', 64);
    lim record;
    filas bigint;
    anterior text;
    actual jsonb;
    id bigint;
BEGIN
    SELECT * INTO lim FROM audit.limites_particion(p_particion);
    IF lim.desde IS NULL OR lim.hasta IS NULL THEN
        RAISE EXCEPTION 'audit.registrar_export_worm: % no es una partición por rango de audit.audit_events', p_particion;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('audit.worm_exports'));

    IF audit.digest_particion(p_particion) <> p_contenido_digest THEN
        RAISE EXCEPTION 'audit.registrar_export_worm: el contenido de % no coincide con el exportado (digest distinto)', p_particion;
    END IF;
    actual := audit.manifiesto_particion(p_particion);
    IF actual <> p_tenants THEN
        RAISE EXCEPTION 'audit.registrar_export_worm: el manifiesto por tenant no coincide con el estado actual de %', p_particion;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(actual) x WHERE NOT (x->>'integra')::boolean) THEN
        RAISE EXCEPTION 'audit.registrar_export_worm: hay cadenas de auditoría no íntegras (audit.verify_chain): no se registra el export';
    END IF;

    SELECT w.archivo_sha256 INTO anterior FROM audit.worm_exports w ORDER BY w.id DESC LIMIT 1;
    IF p_export_anterior_sha256 <> coalesce(anterior, genesis) THEN
        RAISE EXCEPTION 'audit.registrar_export_worm: el export anterior (%) no es el último registrado (%)',
            p_export_anterior_sha256, coalesce(anterior, genesis);
    END IF;

    EXECUTE format('SELECT count(*) FROM %s', p_particion) INTO filas;
    INSERT INTO audit.worm_exports (particion, desde, hasta, filas, contenido_digest, archivo_sha256, manifiesto_sha256,
                                    export_anterior_sha256, archivo_ref, tenants, verificado_por)
    VALUES (p_particion::text, lim.desde, lim.hasta, filas, p_contenido_digest, p_archivo_sha256, p_manifiesto_sha256,
            p_export_anterior_sha256, p_archivo_ref, p_tenants, p_verificado_por)
    RETURNING worm_exports.id INTO id;
    RETURN id;
END $$;
COMMENT ON FUNCTION audit.registrar_export_worm(regclass, text, text, text, text, text, jsonb, text) IS
'KC-DB-10-2: registra el comprobante de export de una partición tras comprobar que no ha cambiado desde el export, que las cadenas son íntegras y que el export anterior es el último registrado. Lo llama db/tools/worm_auditoria.py solo después de verificar el archivo.';

-- ---------------------------------------------------------------------------
-- 5. Comprobación previa al DROP y anclaje
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.comprobante_worm_vigente(p_particion regclass)
RETURNS TABLE (permitido boolean, motivo text)
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    w audit.worm_exports;
    man jsonb;
    malo text;
BEGIN
    permitido := false; motivo := NULL;
    SELECT * INTO w FROM audit.worm_exports e WHERE e.particion = p_particion::text ORDER BY e.id DESC LIMIT 1;
    IF NOT FOUND THEN
        motivo := 'sin comprobante de export WORM verificado (KC-DB-10-2)';
        RETURN NEXT; RETURN;
    END IF;
    IF audit.digest_particion(p_particion) <> w.contenido_digest THEN
        motivo := format('la partición cambió después del export WORM nº %s (digest distinto): exporte de nuevo', w.id);
        RETURN NEXT; RETURN;
    END IF;
    man := audit.manifiesto_particion(p_particion);
    SELECT x->>'tenant_id' INTO malo FROM jsonb_array_elements(man) x WHERE NOT (x->>'integra')::boolean LIMIT 1;
    IF malo IS NOT NULL THEN
        motivo := format('la cadena de auditoría del tenant %s no es íntegra (audit.verify_chain)', malo);
        RETURN NEXT; RETURN;
    END IF;
    SELECT x->>'tenant_id' INTO malo FROM jsonb_array_elements(man) x WHERE NOT (x->>'prefijo')::boolean LIMIT 1;
    IF malo IS NOT NULL THEN
        motivo := format('la partición no es un prefijo de la cadena del tenant %s: descarte antes las particiones más antiguas', malo);
        RETURN NEXT; RETURN;
    END IF;
    permitido := true;
    RETURN NEXT;
END $$;
COMMENT ON FUNCTION audit.comprobante_worm_vigente(regclass) IS
'KC-DB-10-2: ¿se puede descartar la partición? Exige un comprobante de export WORM verificado de su contenido ACTUAL, cadenas íntegras y que sea un prefijo de la cadena de cada tenant (descarte en orden).';

CREATE OR REPLACE FUNCTION audit.anclar_descarte(p_particion regclass) RETURNS integer
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    ok boolean;
    why text;
    x jsonb;
    export_id bigint;
    n integer := 0;
BEGIN
    SELECT c.permitido, c.motivo INTO ok, why FROM audit.comprobante_worm_vigente(p_particion) c;
    IF NOT ok THEN
        RAISE EXCEPTION 'audit.anclar_descarte: no se puede descartar %: %', p_particion, why;
    END IF;
    SELECT e.id INTO export_id FROM audit.worm_exports e WHERE e.particion = p_particion::text ORDER BY e.id DESC LIMIT 1;
    FOR x IN SELECT * FROM jsonb_array_elements(audit.manifiesto_particion(p_particion)) LOOP
        INSERT INTO audit.chain_anchors (tenant_id, eventos_descartados, ultimo_sello_descartado, ultimo_export_id)
        VALUES (x->>'tenant_id', (x->>'eventos')::bigint, x->>'ultimo_sello', export_id)
        ON CONFLICT (tenant_id) DO UPDATE
           SET eventos_descartados = audit.chain_anchors.eventos_descartados + EXCLUDED.eventos_descartados,
               ultimo_sello_descartado = EXCLUDED.ultimo_sello_descartado,
               ultimo_export_id = EXCLUDED.ultimo_export_id,
               actualizado_en = now();
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;
COMMENT ON FUNCTION audit.anclar_descarte(regclass) IS
'KC-DB-10-2: antes de descartar una partición de auditoría, anota por tenant cuántos eventos y qué último sello se descartan, para que audit.verify_chain y V17 sigan dando íntegra la cadena restante. Falla si no hay comprobante vigente.';

REVOKE ALL ON FUNCTION audit.limites_particion(regclass), audit.digest_particion(regclass),
    audit.manifiesto_particion(regclass),
    audit.registrar_export_worm(regclass, text, text, text, text, text, jsonb, text),
    audit.comprobante_worm_vigente(regclass), audit.anclar_descarte(regclass)
    FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 6. El gancho previo al descarte usa el comprobante (rama de auditoría)
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
        SELECT c.permitido, c.motivo INTO permitido, motivo FROM audit.comprobante_worm_vigente(p_particion) c;
    END IF;
    RETURN NEXT;
END $$;
COMMENT ON FUNCTION core.puede_descartar_particion(text, regclass) IS
'KC-DB-10-1 / KC-DB-10-2: gancho previo al descarte de una partición; devuelve (permitido, motivo). webhook_inbox con entregas pendientes: no. audit.audit_events: solo con comprobante de export WORM verificado y vigente (audit.comprobante_worm_vigente).';

-- ---------------------------------------------------------------------------
-- 7. Motor de purga (054) con el anclaje previo al descarte de auditoría
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
                            -- Auditoría: la cadena de cada tenant pierde sus primeros eslabones; se ancla ANTES de
                            -- descartar, en esta misma transacción (si el DROP falla, el ancla se deshace).
                            IF pol.entity_name = 'audit.audit_events' THEN
                                PERFORM audit.anclar_descarte(part.nombre);
                            END IF;
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

-- ---------------------------------------------------------------------------
-- 8. audit.verify_chain: descuenta lo anclado
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
    ancla_sello text;
    ancla_n bigint;
BEGIN
    -- KC-DB-10-2: tras descartar particiones exportadas en WORM, el primer eslabón que queda cuelga del último sello
    -- descartado (el ancla), no del génesis ni de un evento presente.
    SELECT a.ultimo_sello_descartado, a.eventos_descartados INTO ancla_sello, ancla_n
      FROM audit.chain_anchors a WHERE a.tenant_id = p_tenant;

    WITH ev AS (
        SELECT e.id,
               audit.calcular_sello(e) <> e.sello AS sello_malo,
               (e.sello_previo <> genesis
                AND e.sello_previo IS DISTINCT FROM ancla_sello
                AND NOT EXISTS (
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
        SELECT h.eventos - coalesce(ancla_n, 0) INTO eventos_esperados
          FROM audit.chain_heads h WHERE h.tenant_id = p_tenant;
    END IF;

    integra := roto IS NULL
               AND (eventos_esperados IS NULL OR eventos_esperados = n);
    RETURN NEXT;
END $$;
COMMENT ON FUNCTION audit.verify_chain(text, timestamptz, timestamptz) IS
'KC-DB-05-1 / KC-DB-10-2: verifica sello propio, encadenamiento (sello_previo = sello del eslabon anterior, genesis repeat(0,64), o el ultimo sello descartado en audit.chain_anchors) y recuento contra chain_heads menos lo descartado. Usada por validar_integridad (V16/V17) y GET /audit/integrity.';

-- ---------------------------------------------------------------------------
-- 9. core.validar_integridad(): V17 descuenta lo anclado (V01-V26 idénticos a 053)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.validar_integridad()
RETURNS TABLE (chequeo text, descripcion text, problemas bigint)
LANGUAGE plpgsql STABLE AS $$
BEGIN

-- V01 · Suscripción vigente: todo tenant activo debe tener exactamente una.
chequeo := 'V01_suscripcion_vigente';
descripcion := 'Tenants sin suscripción vigente (o con más de una)';
SELECT count(*) INTO problemas FROM (
    SELECT o.id
      FROM core.organizations o
      LEFT JOIN core.subscriptions s
             ON s.tenant_id = o.id AND s.finaliza_el IS NULL
     WHERE o.eliminado_en IS NULL
     GROUP BY o.id
    HAVING count(s.id) <> 1
) q;
RETURN NEXT;

-- V02 · Ticket ⇄ job: la etapa visible del ticket debe corresponder al job activo.
chequeo := 'V02_espejo_ticket_job';
descripcion := 'Tickets activos cuya etapa no corresponde al estado de su job';
SELECT count(*) INTO problemas
  FROM core.tickets t
  JOIN core.jobs j ON j.ticket_id = t.id
             AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
 WHERE t.etapa <> CASE j.estado
        WHEN 'gate1' THEN 'design'
        WHEN 'gate2' THEN 'qa'
        WHEN 'gate3' THEN 'deploy'
        WHEN 'deploy_preflight' THEN 'deploy'
        WHEN 'pendiente_asignacion' THEN 'triage'
        WHEN 'pausado_por_cuota' THEN 'en_cola_por_cuota'
        WHEN 'escalado_humano' THEN t.etapa
        ELSE j.estado END;
RETURN NEXT;

-- V03 · Gate 3 sin gates 1 y 2 (el trigger lo impide; verificación de defensa).
chequeo := 'V03_gate3_sin_previos';
descripcion := 'Aprobaciones de Gate 3 sin aprobación previa de gates 1 y 2';
SELECT count(*) INTO problemas
  FROM core.gate_signatures g3
 WHERE g3.gate = 3 AND g3.accion = 'aprobado'
   AND 2 > (SELECT count(DISTINCT g.gate) FROM core.gate_signatures g
             WHERE g.ticket_id = g3.ticket_id
               AND g.gate IN (1,2) AND g.accion = 'aprobado'
               AND g.creado_en <= g3.creado_en);
RETURN NEXT;

-- V04 · Tickets en producción con las tres firmas.
chequeo := 'V04_produccion_sin_firmas';
descripcion := 'Tickets en producción sin las tres firmas de gates';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa = 'produccion'
   AND 3 > (SELECT count(DISTINCT gate) FROM core.gate_signatures
             WHERE ticket_id = t.id AND accion = 'aprobado');
RETURN NEXT;

-- V05 · Specs: exactamente una alternativa recomendada por versión.
chequeo := 'V05_spec_recomendada';
descripcion := 'Specs sin exactamente una alternativa recomendada';
SELECT count(*) INTO problemas FROM (
    SELECT s.id FROM core.specs s
      LEFT JOIN core.spec_alternatives a ON a.spec_id = s.id AND a.recomendada
     GROUP BY s.id HAVING count(a.alt_id) <> 1
) q;
RETURN NEXT;

-- V06 · Specs aprobadas con alternativa elegida.
chequeo := 'V06_spec_aprobada_sin_eleccion';
descripcion := 'Specs aprobadas (Gate 1) sin alternativa elegida';
SELECT count(*) INTO problemas FROM (
    SELECT s.id FROM core.specs s
     WHERE s.estado = 'aprobada'
       AND NOT EXISTS (SELECT 1 FROM core.spec_alternatives a
                        WHERE a.spec_id = s.id AND a.elegida)
       AND EXISTS (SELECT 1 FROM core.spec_alternatives a WHERE a.spec_id = s.id)
) q;
RETURN NEXT;

-- V07 · Matriz de QA completa: cada corrida cubre todos los criterios de su spec.
chequeo := 'V07_matriz_qa_completa';
descripcion := 'Corridas de QA que no cubren todos los criterios de la spec';
SELECT count(*) INTO problemas FROM (
    SELECT r.id
      FROM core.qa_runs r
      JOIN core.spec_criteria c ON c.spec_id = r.spec_id
      LEFT JOIN core.qa_results q ON q.qa_run_id = r.id AND q.criterio_id = c.criterio_id
     GROUP BY r.id
    HAVING count(c.criterio_id) <> count(q.criterio_id)
) q;
RETURN NEXT;

-- V08 · Resultados de QA que referencian criterios inexistentes en la spec.
chequeo := 'V08_qa_criterio_fantasma';
descripcion := 'Resultados de QA con criterio_id que no existe en la spec de la corrida';
SELECT count(*) INTO problemas
  FROM core.qa_results q
  JOIN core.qa_runs r ON r.id = q.qa_run_id
 WHERE NOT EXISTS (SELECT 1 FROM core.spec_criteria c
                    WHERE c.spec_id = r.spec_id AND c.criterio_id = q.criterio_id);
RETURN NEXT;

-- V09 · Deploy bloqueado: tickets en produccion con hallazgos bloqueantes abiertos.
chequeo := 'V09_produccion_con_bloqueantes';
descripcion := 'Tickets en producción con hallazgos de seguridad bloqueantes abiertos';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa = 'produccion'
   AND EXISTS (SELECT 1 FROM core.security_findings f
                WHERE f.ticket_id = t.id AND f.bloquea AND f.estado = 'abierto');
RETURN NEXT;

-- V10 · Agregado de cuota: usage_cycles debe cuadrar con la suma real de usage_loc.
--       La suma real puede ser negativa (créditos mayores que lo consumido);
--       el piso en cero se aplica solo al total del ciclo (KC-DB-05-3, DB-F07).
chequeo := 'V10_cuota_cuadra';
descripcion := 'Ciclos cuyo agregado no cuadra con la suma de usage_loc (netas reales, piso en cero solo en el total)';
SELECT count(*) INTO problemas FROM (
    SELECT c.tenant_id, c.ciclo
      FROM core.usage_cycles c
      LEFT JOIN core.usage_loc l ON l.tenant_id = c.tenant_id AND l.ciclo = c.ciclo
     GROUP BY c.tenant_id, c.ciclo, c.loc_netas, c.loc_consumidas
    HAVING c.loc_netas <> coalesce(sum(l.netas), 0)
        OR c.loc_consumidas <> greatest(coalesce(sum(l.netas), 0), 0)
) q;
RETURN NEXT;

-- V11 · Líneas: coherencia production_lines ⇄ jobs.
chequeo := 'V11_lineas_coherentes';
descripcion := 'Líneas ocupadas sin job activo o jobs con línea no reflejada';
SELECT count(*) INTO problemas FROM (
    SELECT pl.tenant_id, pl.numero
      FROM core.production_lines pl
      LEFT JOIN core.jobs j ON j.id = pl.job_id
             AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
     WHERE (pl.estado = 'ocupada') <> (j.id IS NOT NULL)
    UNION ALL
    SELECT j.tenant_id, j.linea
      FROM core.jobs j
      LEFT JOIN core.production_lines pl
             ON pl.tenant_id = j.tenant_id AND pl.numero = j.linea AND pl.job_id = j.id
     WHERE j.linea IS NOT NULL
       AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
       AND pl.job_id IS NULL
) q;
RETURN NEXT;

-- V12 · Líneas concurrentes dentro del plan (QU-2).
chequeo := 'V12_lineas_dentro_del_plan';
descripcion := 'Tenants con más jobs en línea que líneas de su plan';
SELECT count(*) INTO problemas FROM (
    SELECT j.tenant_id
      FROM core.jobs j
      JOIN core.subscriptions s ON s.tenant_id = j.tenant_id AND s.finaliza_el IS NULL
      JOIN core.plans p ON p.codigo = s.plan_codigo
     WHERE j.linea IS NOT NULL
       AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
     GROUP BY j.tenant_id, p.lineas
    HAVING p.lineas IS NOT NULL AND count(*) > p.lineas
) q;
RETURN NEXT;

-- V13 · Duplicados: cadena sin ciclos de un nivel y sin duplicado_de colgante.
chequeo := 'V13_duplicados_validos';
descripcion := 'Tickets duplicados que apuntan a otro duplicado (cadenas)';
SELECT count(*) INTO problemas
  FROM core.tickets t
  JOIN core.tickets d ON d.id = t.duplicado_de
 WHERE d.duplicado_de IS NOT NULL;
RETURN NEXT;

-- V14 · Embeddings huérfanos o faltantes (vec ⇄ core).
chequeo := 'V14_embeddings_tickets';
descripcion := 'Tickets activos (>1 h) sin embedding de deduplicación';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa NOT IN ('cancelado','cerrado_duplicado')
   AND t.creado_en < now() - interval '1 hour'
   AND NOT EXISTS (SELECT 1 FROM vec.ticket_embeddings e WHERE e.ticket_id = t.id);
RETURN NEXT;

-- V15 · Dimensión de embeddings homogénea (1024).
chequeo := 'V15_dimension_embeddings';
descripcion := 'Embeddings con dimensión distinta de 1024';
SELECT (SELECT count(*) FROM vec.ticket_embeddings     WHERE vector_dims(embedding) <> 1024)
     + (SELECT count(*) FROM vec.doc_chunks            WHERE vector_dims(embedding) <> 1024)
     + (SELECT count(*) FROM vec.definition_embeddings WHERE vector_dims(embedding) <> 1024)
  INTO problemas;
RETURN NEXT;

-- V16 · Cadena de auditoría íntegra (recorre todos los tenants visibles):
--       sello propio, encadenamiento (sello_previo = sello del eslabón anterior)
--       y recuento contra chain_heads (KC-DB-05-1, DB-F04).
chequeo := 'V16_cadena_auditoria';
descripcion := 'Tenants con cadena de auditoría rota: sello alterado, eslabón perdido o eventos faltantes (ver audit.verify_chain)';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h,
           LATERAL audit.verify_chain(h.tenant_id) v
     WHERE NOT v.integra
) q;
RETURN NEXT;

-- V17 · Cabeza de cadena consistente con los eventos: mismo último sello y
--       mismo número de eventos (un hueco por borrado delata la diferencia),
--       y ningún tenant con eventos sin cabeza (KC-DB-05-1, DB-F04).
chequeo := 'V17_cabeza_cadena';
descripcion := 'chain_heads que no cuadra con los eventos: último sello distinto, eventos de menos o de más, o eventos sin cabeza';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h
      LEFT JOIN LATERAL (
          SELECT count(*) AS n,
                 (array_agg(e.sello ORDER BY e.id DESC))[1] AS ultimo
            FROM audit.audit_events e
           WHERE e.tenant_id = h.tenant_id) ev ON true
      LEFT JOIN audit.chain_anchors an ON an.tenant_id = h.tenant_id
     WHERE ev.n <> h.eventos - coalesce(an.eventos_descartados, 0)
        OR ev.ultimo IS DISTINCT FROM
           (CASE WHEN h.eventos - coalesce(an.eventos_descartados, 0) > 0 THEN h.ultimo_sello END)
    UNION
    SELECT e.tenant_id
      FROM audit.audit_events e
     WHERE NOT EXISTS (SELECT 1 FROM audit.chain_heads h WHERE h.tenant_id = e.tenant_id)
) q;
RETURN NEXT;

-- V18 · Webhooks: fallidos estancados (>24 h sin reproceso).
chequeo := 'V18_webhooks_estancados';
descripcion := 'Entradas de webhook_inbox fallidas hace más de 24 h';
SELECT count(*) INTO problemas
  FROM core.webhook_inbox
 WHERE estado = 'fallido' AND recibido_en < now() - interval '24 hours';
RETURN NEXT;

-- V19 · Adjuntos huérfanos vencidos (>48 h sin asociar): candidatos a purga.
chequeo := 'V19_adjuntos_huerfanos';
descripcion := 'Adjuntos sin ticket asociado hace más de 48 h';
SELECT count(*) INTO problemas
  FROM core.ticket_attachments
 WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours';
RETURN NEXT;

-- V20 · Gates aprobados apuntando a specs de otro ticket.
chequeo := 'V20_gate_spec_coherente';
descripcion := 'Firmas de Gate 1 cuya spec no pertenece al mismo ticket';
SELECT count(*) INTO problemas
  FROM core.gate_signatures g
  JOIN core.specs s ON s.id = g.spec_id
 WHERE g.spec_id IS NOT NULL AND s.ticket_id <> g.ticket_id;
RETURN NEXT;


-- V21 · Coherencia de tenant padre/hijo (KC-DB-03-3, DB-F01). Las relaciones se
--       generan desde pg_constraint: ver core.incoherencias_tenant().
chequeo := 'V21_coherencia_tenant';
SELECT count(*),
       'Filas cuyo tenant no coincide con el de su padre'
       || coalesce(' en: ' || string_agg(DISTINCT relacion || ' (' || tabla_hija || ')', ', '), '')
  INTO problemas, descripcion
  FROM core.incoherencias_tenant();
RETURN NEXT;

-- V22 · Ningún ciclo de cuota puede tener loc_consumidas negativo (KC-DB-05-3, DB-F07).
chequeo := 'V22_consumo_no_negativo';
descripcion := 'Ciclos de cuota con loc_consumidas negativo';
SELECT count(*) INTO problemas FROM core.usage_cycles WHERE loc_consumidas < 0;
RETURN NEXT;

-- V23 · Renovación vencida: suscripciones activas cuyo renueva_el pasó (antes de hoy)
--       sin que core.renovar_ciclo abriera el ciclo nuevo (KC-DB-09-1, DB-F08).
--       Se compara con < current_date para no alertar por las renovaciones del día
--       que el worker horario aún no ha procesado.
chequeo := 'V23_renovacion_vencida';
descripcion := 'Suscripciones activas con renueva_el vencido sin ciclo nuevo (ver core.renovar_ciclo)';
SELECT count(*) INTO problemas
  FROM core.subscriptions s
 WHERE s.finaliza_el IS NULL
   AND s.deleted_at IS NULL
   AND s.estado IN ('activa','periodo_gracia')
   AND s.renueva_el < current_date;
RETURN NEXT;

-- V24 · Hallazgos de seguridad bloqueantes sin adjudicar (estado 'abierto': ni corregidos ni descartados con
--       justificación) desde hace más de 48 h (KC-DB-09-6). V09 ya impide pasar a producción con bloqueantes;
--       éste detecta los que llevan días esperando a alguien.
chequeo := 'V24_bloqueantes_sin_adjudicar';
descripcion := 'Hallazgos de seguridad bloqueantes abiertos hace más de 48 h sin corregir ni descartar';
SELECT count(*) INTO problemas
  FROM core.security_findings f
 WHERE f.bloquea AND f.estado = 'abierto'
   AND f.creado_en < now() - interval '48 hours';
RETURN NEXT;

-- V25 · Organizaciones borradas lógicamente (deleted_at, o eliminado_en legado) con actividad POSTERIOR al
--       borrado: tickets, trabajos o consumo de LOC creados después. Una organización borrada no debe operar;
--       si hay filas nuevas, el borrado no se aplicó en algún camino de escritura. La auditoría no cuenta
--       (core.suprimir_tenant registra su propio acto después de marcar el borrado). Cuenta organizaciones.
chequeo := 'V25_org_borrada_con_actividad';
descripcion := 'Organizaciones borradas lógicamente con tickets, trabajos o consumo creados después del borrado';
SELECT count(*) INTO problemas
  FROM core.organizations o
 WHERE coalesce(o.deleted_at, o.eliminado_en) IS NOT NULL
   AND (EXISTS (SELECT 1 FROM core.tickets t
                 WHERE t.tenant_id = o.id AND t.creado_en > coalesce(o.deleted_at, o.eliminado_en))
     OR EXISTS (SELECT 1 FROM core.jobs j
                 WHERE j.tenant_id = o.id AND j.creado_en > coalesce(o.deleted_at, o.eliminado_en))
     OR EXISTS (SELECT 1 FROM core.usage_loc u
                 WHERE u.tenant_id = o.id AND u.creado_en > coalesce(o.deleted_at, o.eliminado_en)));
RETURN NEXT;

-- V26 · Tablas particionadas sin política de retención declarada (KC-DB-09-6): ni una ventana en
--       core.retencion_particiones ni una declaración en core.retencion_declaraciones (que incluye «conservar»
--       con su justificación). Una tabla que crece sin que nadie haya decidido cuánto conservarla es deuda.
chequeo := 'V26_tabla_sin_retencion';
descripcion := 'Tablas particionadas sin política de retención declarada (core.retencion_particiones o core.retencion_declaraciones)';
SELECT core.contar_tablas_sin_retencion() INTO problemas;
RETURN NEXT;

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1; descuentan los eventos descartados tras un export WORM, anclados en audit.chain_anchors, KC-DB-10-2); V10/V12 la validación comercial (V10 contra la suma real de usage_loc, V22 sin consumo negativo, KC-DB-05-3); V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0); V23 las renovaciones por aniversario vencidas (KC-DB-09-1); V24 los hallazgos bloqueantes abiertos más de 48 h; V25 las organizaciones borradas con actividad posterior; V26 las tablas particionadas sin retención declarada (KC-DB-09-6). 26 chequeos en total.';


-- ---------------------------------------------------------------------------
-- 10. Qué particiones de auditoría hay y su último comprobante
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW audit.v_particiones_worm AS
SELECT c.oid::regclass::text AS particion,
       l.desde, l.hasta,
       (SELECT max(w.id) FROM audit.worm_exports w WHERE w.particion = c.oid::regclass::text) AS ultimo_export_id
  FROM pg_inherits i
  JOIN pg_class c ON c.oid = i.inhrelid
  CROSS JOIN LATERAL audit.limites_particion(c.oid) l
 WHERE l.hasta IS NOT NULL
   AND EXISTS (SELECT 1 FROM pg_class p JOIN pg_namespace pn ON pn.oid = p.relnamespace
                WHERE p.oid = i.inhparent AND pn.nspname = 'audit' AND p.relname = 'audit_events')
 ORDER BY l.desde;
COMMENT ON VIEW audit.v_particiones_worm IS
'KC-DB-10-2: particiones por rango de audit.audit_events y el id de su último comprobante de export WORM (NULL = sin exportar). Sin literales regclass: no estorba a db/tools/revertir_particion_auditoria.sql.';
REVOKE ALL ON audit.v_particiones_worm FROM PUBLIC, kallicode_app, kallicode_worker;
