-- ============================================================================
-- KALLICODE · db/tools/smoke_release.sql
-- KC-DB-12-5 — Smoke de release (aceptación de GA): ejecuta core.smoke_release() y falla si algo no cuadra.
--
-- Se ejecuta con el rol ADMINISTRADOR (verificar la cadena de todos los tenants exige ver todos los tenants), después de
-- aplicar las migraciones y ANTES de dar el despliegue por bueno:
--
--   psql "$KC_DATABASE_URL_MIGRATIONS" -v ON_ERROR_STOP=1 \
--        -v version_esperada=v2.0.0 \
--        -v ultima_migracion=070_smoke_release.sql \
--        -v migraciones=68 \
--        -f db/tools/smoke_release.sql
--
-- version_esperada: la etiqueta de db/esquema_v2.snapshot.json. ultima_migracion: el último archivo de db/migrations en el SHA
-- desplegado. migraciones: cuántos archivos .sql trae ese SHA (ls db/migrations/*.sql | wc -l). Opcionalmente
-- -v chequeos=26 para el número de chequeos de core.validar_integridad() (26 por defecto).
-- Complementa a db/tools/smoke_post_deploy.sql (que comprueba que la APLICACIÓN puede leer su versión) y no lo sustituye.
-- Sale con código distinto de 0 si alguna comprobación falla.
-- ============================================================================

\if :{?chequeos}
\else
\set chequeos 26
\endif

\echo === smoke de release ===
SELECT chequeo, CASE WHEN ok THEN 'OK' ELSE 'FALLA' END AS estado, detalle
  FROM core.smoke_release(:'version_esperada', :'ultima_migracion', :migraciones, :chequeos);

SELECT count(*) FILTER (WHERE NOT ok) AS fallos
  FROM core.smoke_release(:'version_esperada', :'ultima_migracion', :migraciones, :chequeos) \gset

SELECT set_config('kc.smoke_fallos', :'fallos', false) \gset ignorar_
DO $$
BEGIN
    IF current_setting('kc.smoke_fallos')::integer > 0 THEN
        RAISE EXCEPTION 'smoke de release: % comprobación(es) fallan (ver la tabla de arriba)', current_setting('kc.smoke_fallos');
    END IF;
    RAISE NOTICE 'smoke de release OK: todas las comprobaciones pasan';
END
$$;
