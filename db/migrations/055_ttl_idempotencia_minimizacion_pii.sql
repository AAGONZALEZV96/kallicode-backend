-- ============================================================================
-- KALLICODE · 055_ttl_idempotencia_minimizacion_pii.sql
-- KC-DB-10-3 (5 SP) — Expiración por TTL de core.idempotency_keys y minimización de PII.
--
-- 1. TTL de idempotencia. La purga vivía como un DELETE suelto con «48 hours» escrito en
--    apps/workers/.../housekeeping.py. Ahora es core.purgar_idempotency_keys(): una sola fuente del TTL,
--    medible (core.purgas_log) y programable con pg_cron. Solo borra claves cuyo creado_en es anterior a
--    ahora - TTL; las claves dentro de su ventana no se tocan. El TTL mínimo es 1 h: una reserva en curso
--    del worker de ingesta (KC-CM-10-1, status_code = 0) renueva creado_en con su latido, así que una
--    reserva viva nunca está cerca del límite.
--
-- 2. Minimización de PII. Anonimiza datos personales que ya no tienen un uso operativo, con ventanas
--    configurables en core.pii_ventanas (la lógica queda fija en la función, revisable):
--      refresh_tokens_red   core.refresh_tokens.ip y user_agent        30 días desde emitido_en
--      invitaciones_email   core.invitations.email (estado distinto de 'enviada')   90 días desde creado_en
--    NO se toca audit.audit_events: su ip y user_agent forman parte del sello de la cadena de hashes
--    (versión 2) y anonimizarlos la rompería. Tampoco las tablas particionadas de colas
--    (webhook_inbox, notifications): se eliminan enteras por partición (036).
--
-- 3. core.purgas_log registra cada ejecución (tarea, filas, fecha): evidencia para el informe de
--    cumplimiento (KC-DB-10-4) y el ensayo de retención (KC-DB-10-6).
--
-- Aditiva y re-ejecutable. Sin índices nuevos. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ----------------------------------------------------------------------------
-- 1. Registro de ejecuciones y ventanas de minimización (tablas de sistema: sin tenant_id).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.purgas_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tarea         text        NOT NULL,
    filas         bigint      NOT NULL,
    ejecutada_en  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT purgas_log_filas_chk CHECK (filas >= 0)
);
COMMENT ON TABLE core.purgas_log IS
'KC-DB-10-3: una fila por ejecución de core.purgar_idempotency_keys() y de cada regla de core.minimizar_pii(). Alimenta el informe de «fecha de última purga» (KC-DB-10-4).';
REVOKE ALL ON core.purgas_log FROM PUBLIC, kallicode_app, kallicode_worker;

CREATE TABLE IF NOT EXISTS core.pii_ventanas (
    regla       text PRIMARY KEY,
    ventana     interval    NOT NULL,
    activa      boolean     NOT NULL DEFAULT true,
    descripcion text        NOT NULL,
    CONSTRAINT pii_ventanas_ventana_chk CHECK (ventana >= interval '1 hour')
);
COMMENT ON TABLE core.pii_ventanas IS
'KC-DB-10-3: ventana operativa de cada regla de core.minimizar_pii(). Pasado ese plazo, el dato personal se anonimiza. Ajustar la ventana es una decisión de negocio, no de código.';
REVOKE ALL ON core.pii_ventanas FROM PUBLIC, kallicode_app, kallicode_worker;

INSERT INTO core.pii_ventanas (regla, ventana, descripcion) VALUES
    ('refresh_tokens_red',
     interval '30 days',
     'core.refresh_tokens.ip y user_agent pasan a NULL 30 días después de emitido_en.'),
    ('invitaciones_email',
     interval '90 days',
     'core.invitations.email de invitaciones aceptadas, expiradas o revocadas se reemplaza por un valor anónimo 90 días después de creado_en.')
ON CONFLICT (regla) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. Purga por TTL de las claves de idempotencia.
--    SECURITY DEFINER: se ejecuta como kallicode_admin (BYPASSRLS), así que barre todos los tenants y puede
--    escribir en el registro sin darle más privilegios a kallicode_worker, que solo recibe EXECUTE.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.purgar_idempotency_keys(
    p_ahora timestamptz DEFAULT now(),
    p_ttl   interval    DEFAULT interval '48 hours'
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    v_filas bigint;
BEGIN
    IF p_ttl IS NULL OR p_ttl < interval '1 hour' THEN
        RAISE EXCEPTION 'TTL de idempotencia inválido (%): el mínimo es 1 hora', p_ttl
            USING ERRCODE = '22023';
    END IF;

    DELETE FROM core.idempotency_keys
     WHERE creado_en < p_ahora - p_ttl;
    GET DIAGNOSTICS v_filas = ROW_COUNT;

    INSERT INTO core.purgas_log (tarea, filas) VALUES ('idempotency_keys', v_filas);
    RETURN v_filas;
END
$f$;
COMMENT ON FUNCTION core.purgar_idempotency_keys(timestamptz, interval) IS
'KC-DB-10-3: elimina las claves de core.idempotency_keys con creado_en anterior a p_ahora - p_ttl (48 h por defecto, mínimo 1 h) en todos los tenants y devuelve cuántas. Las claves dentro de su ventana no se tocan.';
REVOKE ALL ON FUNCTION core.purgar_idempotency_keys(timestamptz, interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.purgar_idempotency_keys(timestamptz, interval)
    TO kallicode_worker, kallicode_admin;

-- ----------------------------------------------------------------------------
-- 3. Minimización de PII. Devuelve las filas anonimizadas por regla; es idempotente (una fila ya
--    anonimizada no vuelve a contarse).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.minimizar_pii(p_ahora timestamptz DEFAULT now())
RETURNS TABLE (regla text, filas bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    v_ventana interval;
    v_filas   bigint;
BEGIN
    -- Dirección IP y navegador de las sesiones: útiles para detectar un uso anómalo mientras la sesión es
    -- reciente; después son datos personales sin finalidad.
    SELECT v.ventana INTO v_ventana
      FROM core.pii_ventanas v WHERE v.regla = 'refresh_tokens_red' AND v.activa;
    IF FOUND THEN
        UPDATE core.refresh_tokens rt
           SET ip = NULL, user_agent = NULL
         WHERE rt.emitido_en < p_ahora - v_ventana
           AND (rt.ip IS NOT NULL OR rt.user_agent IS NOT NULL);
        GET DIAGNOSTICS v_filas = ROW_COUNT;
        INSERT INTO core.purgas_log (tarea, filas) VALUES ('pii:refresh_tokens_red', v_filas);
        regla := 'refresh_tokens_red'; filas := v_filas; RETURN NEXT;
    END IF;

    -- Email de invitaciones ya cerradas: quien nunca aceptó no tiene cuenta, y quien aceptó ya tiene su
    -- email en core.users. La fila se conserva (el historial de invitaciones sigue contando).
    SELECT v.ventana INTO v_ventana
      FROM core.pii_ventanas v WHERE v.regla = 'invitaciones_email' AND v.activa;
    IF FOUND THEN
        UPDATE core.invitations i
           SET email = 'anonimizado-' || i.id || '@anonimo.invalid'
         WHERE i.estado <> 'enviada'
           AND i.creado_en < p_ahora - v_ventana
           AND i.email NOT LIKE 'anonimizado-%@anonimo.invalid';
        GET DIAGNOSTICS v_filas = ROW_COUNT;
        INSERT INTO core.purgas_log (tarea, filas) VALUES ('pii:invitaciones_email', v_filas);
        regla := 'invitaciones_email'; filas := v_filas; RETURN NEXT;
    END IF;
END
$f$;
COMMENT ON FUNCTION core.minimizar_pii(timestamptz) IS
'KC-DB-10-3: anonimiza PII fuera de su ventana operativa (core.pii_ventanas): ip y user_agent de refresh_tokens, email de invitaciones cerradas. No toca audit.audit_events (sellado por hash). Idempotente; devuelve las filas anonimizadas por regla.';
REVOKE ALL ON FUNCTION core.minimizar_pii(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.minimizar_pii(timestamptz) TO kallicode_admin;

-- ----------------------------------------------------------------------------
-- 4. Programación con pg_cron (como en 044: si no está instalada o falta el permiso, se avisa y el
--    housekeeping del worker sigue purgando las claves).
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regnamespace('cron') IS NULL THEN
        RAISE NOTICE '055: pg_cron no está instalada: no se programan la purga de idempotencia ni la minimización de PII (las claves las sigue purgando el housekeeping del worker).';
        RETURN;
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'cron', 'USAGE') THEN
        RAISE NOTICE '055: kallicode_admin no tiene USAGE sobre el esquema cron: vuelva a ejecutar db/00_extensiones.sql como superusuario y programe los trabajos a mano (docs/retencion_ttl_pii.md).';
        RETURN;
    END IF;
    BEGIN
        PERFORM cron.schedule('idempotencia_purga', '10 * * * *', $c$SELECT core.purgar_idempotency_keys()$c$);
        PERFORM cron.schedule('pii_minimizar',      '30 3 * * *', $c$SELECT * FROM core.minimizar_pii()$c$);
    EXCEPTION WHEN OTHERS THEN
        -- Típico: pg_cron instalada pero sin cargar en shared_preload_libraries. Programar es operativo,
        -- no debe impedir crear las funciones.
        RAISE NOTICE '055: no se pudo programar con pg_cron (%): programe los trabajos a mano (docs/retencion_ttl_pii.md).', SQLERRM;
    END;
END $$;
