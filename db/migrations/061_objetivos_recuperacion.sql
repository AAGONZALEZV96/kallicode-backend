-- ============================================================================
-- KALLICODE · 061_objetivos_recuperacion.sql
-- KC-DB-11-1 (8 SP) — RPO/RTO declarados y ensayo de restauración PITR medido.
--
-- 1. core.objetivos_recuperacion: los objetivos de recuperación DECLARADOS (RPO y RTO en minutos) con su estado.
--    Se siembra la propuesta del ticket (RPO 5 min, RTO 60 min) en estado 'propuesto': que los objetivos estén
--    APROBADOS por el Architecture Board es una decisión humana, no una consecuencia del ensayo. Solo puede haber
--    uno aprobado a la vez; aprobar uno nuevo deja el anterior como 'sustituido'. El acta del ensayo
--    (docs/recuperacion_pitr.md) se compara contra el objetivo vigente (aprobado, o en su defecto el propuesto).
--
-- 2. core.aprobar_objetivos_recuperacion(): único camino para pasar un objetivo a 'aprobado' (exige quién y la
--    referencia del acta). Solo el rol administrador puede ejecutarla.
--
-- 3. core.estado_archivado_wal(): estado del archivado de WAL (la base del RPO: sin archivado no hay punto en el
--    tiempo posterior a la última copia). Lee pg_stat_archiver; no necesita permisos especiales. Es la medida que
--    una alerta debe vigilar (docs/recuperacion_pitr.md §5).
--
-- Aditiva y re-ejecutable. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE TABLE IF NOT EXISTS core.objetivos_recuperacion (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rpo_minutos    integer     NOT NULL,
    rto_minutos    integer     NOT NULL,
    estado         text        NOT NULL DEFAULT 'propuesto',
    propuesto_por  text        NOT NULL,
    aprobado_por   text,
    aprobado_en    timestamptz,
    acta_ref       text,
    nota           text,
    creado_en      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT objetivos_recuperacion_rpo_chk CHECK (rpo_minutos BETWEEN 1 AND 1440),
    CONSTRAINT objetivos_recuperacion_rto_chk CHECK (rto_minutos BETWEEN 1 AND 10080),
    CONSTRAINT objetivos_recuperacion_estado_chk CHECK (estado IN ('propuesto', 'aprobado', 'sustituido')),
    CONSTRAINT objetivos_recuperacion_aprobacion_chk CHECK (
        estado <> 'aprobado' OR (aprobado_por IS NOT NULL AND aprobado_en IS NOT NULL AND acta_ref IS NOT NULL)
    )
);
COMMENT ON TABLE core.objetivos_recuperacion IS
'KC-DB-11-1: objetivos de recuperación declarados (RPO/RTO en minutos). Una fila aprobada exige quién, cuándo y la referencia del acta del Architecture Board; solo una puede estar aprobada a la vez.';
REVOKE ALL ON core.objetivos_recuperacion FROM PUBLIC, kallicode_app, kallicode_worker;

CREATE UNIQUE INDEX IF NOT EXISTS objetivos_recuperacion_aprobado_uq
    ON core.objetivos_recuperacion ((true)) WHERE estado = 'aprobado';

INSERT INTO core.objetivos_recuperacion (rpo_minutos, rto_minutos, estado, propuesto_por, nota)
SELECT 5, 60, 'propuesto', 'KC-DB-11-1',
       'Propuesta del ticket KC-DB-11-1: RPO <= 5 min y RTO <= 60 min. Pendiente de aprobación del Architecture Board.'
 WHERE NOT EXISTS (SELECT 1 FROM core.objetivos_recuperacion);

CREATE OR REPLACE FUNCTION core.aprobar_objetivos_recuperacion(p_id bigint, p_por text, p_acta text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
    IF coalesce(btrim(p_por), '') = '' OR coalesce(btrim(p_acta), '') = '' THEN
        RAISE EXCEPTION 'aprobar_objetivos_recuperacion: hay que indicar quién aprueba y la referencia del acta'
            USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.objetivos_recuperacion WHERE id = p_id AND estado = 'propuesto') THEN
        RAISE EXCEPTION 'aprobar_objetivos_recuperacion: % no existe o no está en estado propuesto', p_id
            USING ERRCODE = '22023';
    END IF;
    UPDATE core.objetivos_recuperacion SET estado = 'sustituido' WHERE estado = 'aprobado';
    UPDATE core.objetivos_recuperacion
       SET estado = 'aprobado', aprobado_por = p_por, aprobado_en = now(), acta_ref = p_acta
     WHERE id = p_id;
END
$f$;
COMMENT ON FUNCTION core.aprobar_objetivos_recuperacion(bigint, text, text) IS
'KC-DB-11-1: pasa unos objetivos propuestos a aprobados (quién y acta obligatorios) y sustituye los anteriores. Solo kallicode_admin.';
REVOKE ALL ON FUNCTION core.aprobar_objetivos_recuperacion(bigint, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.aprobar_objetivos_recuperacion(bigint, text, text) TO kallicode_admin;

-- ----------------------------------------------------------------------------
-- Estado del archivado de WAL. archivado_al_dia = el archivado está activado y el último intento no falló
-- (o fue posterior al último fallo). Con la base inactiva no hay WAL nuevo que archivar: segundos_desde_ultimo
-- crece sin que sea un fallo, por eso el criterio de alerta es «archivado_al_dia = false», no la antigüedad.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.estado_archivado_wal()
RETURNS TABLE (
    archivado_activado     boolean,
    wal_level              text,
    archive_timeout_s      integer,
    archivado_al_dia       boolean,
    ultimo_wal_archivado   text,
    ultimo_archivado_en    timestamptz,
    segundos_desde_ultimo  numeric,
    archivados             bigint,
    fallos                 bigint,
    ultimo_fallo_en        timestamptz
)
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT current_setting('archive_mode') IN ('on', 'always'),
           current_setting('wal_level'),
           (SELECT setting::integer FROM pg_settings WHERE name = 'archive_timeout'),
           current_setting('archive_mode') IN ('on', 'always')
               AND (a.last_failed_time IS NULL OR a.last_archived_time > a.last_failed_time),
           a.last_archived_wal,
           a.last_archived_time,
           round(extract(epoch FROM now() - a.last_archived_time)::numeric, 1),
           a.archived_count,
           a.failed_count,
           a.last_failed_time
      FROM pg_stat_archiver a
$f$;
COMMENT ON FUNCTION core.estado_archivado_wal() IS
'KC-DB-11-1: estado del archivado de WAL (pg_stat_archiver). Alertar si archivado_activado o archivado_al_dia son false: sin archivado continuo no hay recuperación a un punto en el tiempo y el RPO deja de estar garantizado.';
REVOKE ALL ON FUNCTION core.estado_archivado_wal() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.estado_archivado_wal() TO kallicode_admin;

INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo) VALUES
    (NULL, 'core.objetivos_recuperacion', NULL, 'conservar',
     'Objetivos RPO/RTO declarados y su aprobación (KC-DB-11-1): historial de gobierno, pocas filas, sin plazo propio.')
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;
