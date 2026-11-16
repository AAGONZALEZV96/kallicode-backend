-- ============================================================================
-- KALLICODE · 065_alertas_db.sql
-- KC-DB-11-5 (3 SP) — Runbook de incidentes de base de datos: catálogo de alertas.
--
-- El runbook (docs/runbook_incidentes_db.md) consolida seis incidentes y cada procedimiento tiene que estar vinculado a
-- la alerta que lo dispara. Hasta ahora esas alertas eran frases sueltas en la documentación («alertar si ... > 0»). Esta
-- función las convierte en una definición ejecutable y única: core.alertas_db() evalúa las seis y, para cada una,
-- devuelve si está activa, el valor medido, el umbral y el procedimiento del runbook que corresponde.
--
--   DB-A01  bloqueo_prolongado           sesiones esperando un bloqueo más de N s                      -> I1
--   DB-A02  conexiones_agotadas          conexiones cerca de max_connections o transacciones colgadas   -> I2
--   DB-A03  cadena_auditoria_rota        V16 / V17 de core.validar_integridad() > 0                    -> I3
--   DB-A04  integridad_con_hallazgos     cualquier otro chequeo de core.validar_integridad() > 0       -> I4
--   DB-A05  hnsw_hinchado                hinchazón de algún índice HNSW sobre el umbral                -> I5
--   DB-A06  particion_default_con_filas  alguna partición DEFAULT de core/audit/vec/cm con filas       -> I6
--
-- QUÉ ES Y QUÉ NO ES. Es la DEFINICIÓN y la evaluación de las alertas, consultable por un planificador (pg_cron, el
-- job nocturno del CI, un exporter de Prometheus con una consulta personalizada). NO envía nada a nadie: no hay en el
-- repositorio un Alertmanager ni un canal de guardia, así que conectar el resultado a una notificación queda fuera de
-- esta migración (docs/runbook_incidentes_db.md §«Qué falta»).
--
-- COSTE. A01, A02, A05 y A06 son baratas y pueden evaluarse cada minuto. A03 y A04 ejecutan core.validar_integridad(),
-- que recorre las tablas de negocio y la cadena de auditoría: solo se evalúan con p_integridad = true (ejecución
-- nocturna). Sin él devuelven evaluada = false, nunca «sin problemas».
--
-- PERMISOS. Lee pg_stat_activity, y sin pg_read_all_stats solo vería las sesiones de su propio rol: por eso
-- db/00_roles.sql concede pg_monitor a kallicode_admin. Si falta, A01 y A02 devuelven evaluada = false (no «ok»).
-- Solo kallicode_admin puede ejecutarla. No devuelve datos de ningún tenant: solo contadores y nombres de tablas.
--
-- Aditiva y re-ejecutable. Sin índices nuevos. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE OR REPLACE FUNCTION core.alertas_db(
    p_umbral_bloqueo_s   integer DEFAULT 30,
    p_umbral_conexiones  numeric DEFAULT 0.8,
    p_umbral_idle_tx_s   integer DEFAULT 300,
    p_umbral_hinchazon   numeric DEFAULT 30,
    p_integridad         boolean DEFAULT false
) RETURNS TABLE (
    alerta     text,
    nombre     text,
    severidad  text,
    evaluada   boolean,
    activa     boolean,
    valor      numeric,
    umbral     numeric,
    detalle    text,
    runbook    text
)
LANGUAGE plpgsql SECURITY DEFINER
-- public al final: core.validar_integridad() usa vector_dims() de pgvector, que vive en public.
SET search_path = pg_catalog, public, pg_temp AS $f$
DECLARE
    v_stats      boolean := pg_has_role(current_user, 'pg_read_all_stats', 'member');
    v_espera     numeric;
    v_n          integer;
    v_bloqueantes text;
    v_cliente    integer;
    v_max        integer;
    v_ratio      numeric;
    v_idle_n     integer;
    v_idle_s     numeric;
    v_v1617      bigint;
    v_otros      bigint;
    v_nombres    text;
    v_hinch      numeric;
    v_indice     text;
    r            record;
    v_hay        boolean;
    v_defaults   text := '';
    v_n_def      integer := 0;
BEGIN
    -- ---- DB-A01 · bloqueo prolongado ------------------------------------------------------------------------
    alerta := 'DB-A01'; nombre := 'bloqueo_prolongado'; severidad := 'critica';
    umbral := p_umbral_bloqueo_s; runbook := 'docs/runbook_incidentes_db.md#i1';
    IF v_stats THEN
        SELECT coalesce(max(extract(epoch FROM now() - a.state_change)), 0), count(*)
          INTO v_espera, v_n
          FROM pg_stat_activity a
         WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' AND a.pid <> pg_backend_pid();
        SELECT string_agg(DISTINCT b::text, ', ') INTO v_bloqueantes
          FROM pg_stat_activity a, unnest(pg_blocking_pids(a.pid)) AS b
         WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' AND a.pid <> pg_backend_pid();
        evaluada := true; valor := round(v_espera, 1); activa := v_espera >= p_umbral_bloqueo_s AND v_n > 0;
        detalle := format('%s sesión(es) esperando un bloqueo; la más antigua, %s s; bloqueada(s) por el pid %s',
                          v_n, round(v_espera, 1), coalesce(v_bloqueantes, '-'));
    ELSE
        evaluada := false; activa := NULL; valor := NULL;
        detalle := 'sin pg_read_all_stats no se ven las sesiones de otros roles (GRANT pg_monitor TO kallicode_admin)';
    END IF;
    RETURN NEXT;

    -- ---- DB-A02 · conexiones agotadas -----------------------------------------------------------------------
    alerta := 'DB-A02'; nombre := 'conexiones_agotadas'; severidad := 'critica';
    umbral := p_umbral_conexiones; runbook := 'docs/runbook_incidentes_db.md#i2';
    IF v_stats THEN
        v_max := current_setting('max_connections')::integer;
        SELECT count(*) INTO v_cliente FROM pg_stat_activity a WHERE a.backend_type = 'client backend';
        SELECT count(*), coalesce(max(extract(epoch FROM now() - a.state_change)), 0)
          INTO v_idle_n, v_idle_s
          FROM pg_stat_activity a
         WHERE a.datname = current_database() AND a.state = 'idle in transaction';
        v_ratio := round(v_cliente::numeric / v_max, 3);
        evaluada := true; valor := v_ratio;
        activa := v_ratio >= p_umbral_conexiones OR (v_idle_n > 0 AND v_idle_s >= p_umbral_idle_tx_s);
        detalle := format('%s de %s conexiones (%s %%); %s en «idle in transaction», la más antigua hace %s s (umbral %s s)',
                          v_cliente, v_max, round(v_ratio * 100, 1), v_idle_n, round(v_idle_s, 1), p_umbral_idle_tx_s);
    ELSE
        evaluada := false; activa := NULL; valor := NULL;
        detalle := 'sin pg_read_all_stats no se ven las sesiones de otros roles (GRANT pg_monitor TO kallicode_admin)';
    END IF;
    RETURN NEXT;

    -- ---- DB-A03 y DB-A04 · integridad (cara: solo con p_integridad) -----------------------------------------
    IF p_integridad THEN
        SELECT coalesce(sum(v.problemas) FILTER (WHERE v.chequeo IN ('V16_cadena_auditoria', 'V17_cabeza_cadena')), 0),
               coalesce(sum(v.problemas) FILTER (WHERE v.chequeo NOT IN ('V16_cadena_auditoria', 'V17_cabeza_cadena')), 0),
               string_agg(v.chequeo || '=' || v.problemas, ', ')
                   FILTER (WHERE v.problemas > 0 AND v.chequeo NOT IN ('V16_cadena_auditoria', 'V17_cabeza_cadena'))
          INTO v_v1617, v_otros, v_nombres
          FROM core.validar_integridad() v;
    END IF;

    alerta := 'DB-A03'; nombre := 'cadena_auditoria_rota'; severidad := 'critica';
    umbral := 0; runbook := 'docs/runbook_incidentes_db.md#i3';
    IF p_integridad THEN
        evaluada := true; valor := v_v1617; activa := v_v1617 > 0;
        detalle := format('V16 + V17 de core.validar_integridad(): %s problema(s)', v_v1617);
    ELSE
        evaluada := false; activa := NULL; valor := NULL;
        detalle := 'cara: se evalúa con p_integridad = true (ejecución nocturna)';
    END IF;
    RETURN NEXT;

    alerta := 'DB-A04'; nombre := 'integridad_con_hallazgos'; severidad := 'alta';
    umbral := 0; runbook := 'docs/runbook_incidentes_db.md#i4';
    IF p_integridad THEN
        evaluada := true; valor := v_otros; activa := v_otros > 0;
        detalle := CASE WHEN v_otros > 0 THEN 'chequeos con hallazgos: ' || v_nombres
                        ELSE 'los demás chequeos de core.validar_integridad() dan 0' END;
    ELSE
        evaluada := false; activa := NULL; valor := NULL;
        detalle := 'cara: se evalúa con p_integridad = true (ejecución nocturna)';
    END IF;
    RETURN NEXT;

    -- ---- DB-A05 · hinchazón de los HNSW ---------------------------------------------------------------------
    alerta := 'DB-A05'; nombre := 'hnsw_hinchado'; severidad := 'media';
    umbral := p_umbral_hinchazon; runbook := 'docs/runbook_incidentes_db.md#i5';
    SELECT e.hinchazon_pct, e.indice INTO v_hinch, v_indice
      FROM vec.estado_hnsw() e WHERE e.hinchazon_pct IS NOT NULL
     ORDER BY e.hinchazon_pct DESC LIMIT 1;
    IF v_indice IS NULL THEN
        evaluada := false; activa := NULL; valor := NULL;
        detalle := 'ningún índice HNSW tiene línea base (vec.medir_hnsw tras una construcción o un REINDEX)';
    ELSE
        evaluada := true; valor := round(v_hinch, 1); activa := v_hinch > p_umbral_hinchazon;
        detalle := format('la mayor hinchazón es la de %s: %s %% sobre su línea base', v_indice, round(v_hinch, 1));
    END IF;
    RETURN NEXT;

    -- ---- DB-A06 · partición DEFAULT con filas ---------------------------------------------------------------
    FOR r IN
        SELECT c.oid::regclass AS rel
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relispartition AND c.relkind = 'r'
           AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT'
           AND n.nspname IN ('core', 'audit', 'vec', 'cm')
         ORDER BY c.oid::regclass::text
    LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s)', r.rel) INTO v_hay;
        IF v_hay THEN
            v_n_def := v_n_def + 1;
            v_defaults := v_defaults || CASE WHEN v_defaults = '' THEN '' ELSE ', ' END || r.rel::text;
        END IF;
    END LOOP;
    alerta := 'DB-A06'; nombre := 'particion_default_con_filas'; severidad := 'alta';
    umbral := 0; runbook := 'docs/runbook_incidentes_db.md#i6';
    evaluada := true; valor := v_n_def; activa := v_n_def > 0;
    detalle := CASE WHEN v_n_def > 0 THEN 'particiones DEFAULT con filas: ' || v_defaults
                    ELSE 'todas las particiones DEFAULT de core, audit, vec y cm están vacías' END;
    RETURN NEXT;
END
$f$;
COMMENT ON FUNCTION core.alertas_db(integer, numeric, integer, numeric, boolean) IS
'KC-DB-11-5: catálogo ejecutable de las seis alertas de base de datos (DB-A01..DB-A06), cada una con su valor, umbral y el procedimiento del runbook (docs/runbook_incidentes_db.md). A03 y A04 solo se evalúan con p_integridad = true. No envía notificaciones. Solo kallicode_admin; no devuelve datos de tenants.';
REVOKE ALL ON FUNCTION core.alertas_db(integer, numeric, integer, numeric, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.alertas_db(integer, numeric, integer, numeric, boolean) TO kallicode_admin;
