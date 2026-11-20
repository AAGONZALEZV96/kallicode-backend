-- ============================================================================
-- KALLICODE · db/tools/smoke_post_deploy.sql
-- KC-DB-12-1 — Smoke post-deploy del esquema: la base desplegada tiene la versión y las migraciones que
-- el artefacto espera.
--
-- Se ejecuta con el rol de la APLICACIÓN (kallicode_app): demuestra además que la API puede leer su
-- versión (core.version_esquema() es SECURITY DEFINER; el ledger le está vedado).
--
--   psql "$KC_DATABASE_URL_APP" -v ON_ERROR_STOP=1 \
--        -v version_esperada=v2.0.0 \
--        -v ultima_migracion=067_version_esquema.sql \
--        -f db/tools/smoke_post_deploy.sql
--
-- version_esperada: la etiqueta de db/esquema_v2.snapshot.json. ultima_migracion: el último archivo de
-- db/migrations en el SHA desplegado. Sale con código distinto de 0 si algo no cuadra.
-- ============================================================================

\set QUIET on
SELECT set_config('kc.smoke_version_esperada', :'version_esperada', false) \gset smoke_
SELECT set_config('kc.smoke_ultima_migracion', :'ultima_migracion', false) \gset smoke_
\set QUIET off

DO $$
DECLARE
    v_esperada text := current_setting('kc.smoke_version_esperada');
    v_ultima   text := current_setting('kc.smoke_ultima_migracion');
    v          record;
BEGIN
    IF to_regprocedure('core.version_esquema()') IS NULL THEN
        RAISE EXCEPTION 'smoke: core.version_esquema() no existe (falta la migración 067)';
    END IF;

    SELECT * INTO v FROM core.version_esquema();

    IF v.etiqueta IS DISTINCT FROM v_esperada THEN
        RAISE EXCEPTION 'smoke: versión del esquema % (fijada por %), se esperaba %',
            coalesce(v.etiqueta, '<ninguna>'), coalesce(v.archivo_etiqueta, '-'), v_esperada;
    END IF;

    IF v.ultima_migracion IS DISTINCT FROM v_ultima THEN
        RAISE EXCEPTION 'smoke: la última migración aplicada es %, el artefacto desplegado trae %',
            coalesce(v.ultima_migracion, '<ninguna>'), v_ultima;
    END IF;

    RAISE NOTICE 'smoke OK: esquema % (%), % migraciones, % posteriores a la etiqueta, última %',
        v.etiqueta, v.archivo_etiqueta, v.migraciones_aplicadas, v.migraciones_posteriores,
        v.ultima_migracion;
END
$$;
