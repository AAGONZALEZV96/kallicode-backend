-- ============================================================================
-- KALLICODE · 067_version_esquema.sql
-- KC-DB-12-1 — Congelación del esquema v2: etiqueta de versión en el ledger y core.version_esquema().
--
-- El esquema se congela como modelo v2 (política de cambios en docs/politica-cambios-esquema.md). La versión
-- no es un número suelto: es una etiqueta (vMAYOR.MENOR.PARCHE) en la fila de core.schema_migrations de la
-- migración que la fija, de modo que «qué versión tiene esta base» y «qué migraciones lleva» salen del
-- mismo ledger que escribe el runner.
--
-- Cómo se etiqueta: el runner ejecuta el archivo y DESPUÉS inserta su fila en la misma transacción, así que
-- una migración no puede hacer UPDATE de su propia fila. En su lugar fija el GUC local de transacción
--     SELECT set_config('kc.etiqueta_version', 'vX.Y.Z', true);
-- y el trigger BEFORE INSERT de este archivo lo copia a la fila del ledger y lo consume. Requisitos: la
-- migración que etiqueta debe ser transaccional (sin «-- kc:sin-transaccion») y no se etiqueta con --adoptar.
--
-- core.version_esquema() la expone a la aplicación (kallicode_app no puede leer el ledger: REVOKE ALL en
-- 04_rls.sql), y la verifica el smoke post-deploy (db/tools/smoke_post_deploy.sql).
--
-- Cambio ADITIVO (clase A): columna nullable, índice parcial sobre una tabla de decenas de filas, trigger y
-- función nuevos. Ninguna fila existente cambia. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ----------------------------------------------------------------------------
-- 1. Columna de etiqueta en el ledger
-- ----------------------------------------------------------------------------
ALTER TABLE core.schema_migrations
    ADD COLUMN IF NOT EXISTS etiqueta_version text;

ALTER TABLE core.schema_migrations DROP CONSTRAINT IF EXISTS sm_etiqueta_version_chk;
ALTER TABLE core.schema_migrations
    ADD CONSTRAINT sm_etiqueta_version_chk
    CHECK (etiqueta_version ~ '^v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$');

-- Una etiqueta identifica una sola migración.
CREATE UNIQUE INDEX IF NOT EXISTS sm_etiqueta_version_uq
    ON core.schema_migrations (etiqueta_version)
    WHERE etiqueta_version IS NOT NULL;

COMMENT ON COLUMN core.schema_migrations.etiqueta_version IS
    'Versión del esquema (vMAYOR.MENOR.PARCHE) que fija esta migración; NULL si no fija ninguna. '
    'La rellena core.trg_schema_migrations_etiqueta desde el GUC kc.etiqueta_version (KC-DB-12-1).';

-- ----------------------------------------------------------------------------
-- 2. Trigger: copia el GUC kc.etiqueta_version a la fila que inserta el runner
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.trg_schema_migrations_etiqueta()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, core
AS $$
DECLARE
    v_etiqueta text := NULLIF(current_setting('kc.etiqueta_version', true), '');
BEGIN
    IF NEW.etiqueta_version IS NULL AND v_etiqueta IS NOT NULL THEN
        NEW.etiqueta_version := v_etiqueta;
    END IF;
    -- Se consume: ninguna otra fila de la misma transacción hereda la etiqueta.
    IF v_etiqueta IS NOT NULL THEN
        PERFORM set_config('kc.etiqueta_version', '', true);
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION core.trg_schema_migrations_etiqueta() IS
    'Trigger BEFORE INSERT de core.schema_migrations: etiqueta la fila con kc.etiqueta_version (KC-DB-12-1).';

DROP TRIGGER IF EXISTS schema_migrations_etiqueta ON core.schema_migrations;
CREATE TRIGGER schema_migrations_etiqueta
    BEFORE INSERT ON core.schema_migrations
    FOR EACH ROW EXECUTE FUNCTION core.trg_schema_migrations_etiqueta();

-- ----------------------------------------------------------------------------
-- 3. core.version_esquema(): versión vigente y migraciones aplicadas después de fijarla
-- ----------------------------------------------------------------------------
-- El orden es el del runner (alfabético por archivo). migraciones_posteriores > 0 significa que la base
-- lleva cambios aditivos posteriores a la etiqueta (permitidos por la política) sin una versión nueva.
CREATE OR REPLACE FUNCTION core.version_esquema()
RETURNS TABLE (
    etiqueta                text,
    archivo_etiqueta        text,
    etiquetada_en           timestamptz,
    ultima_migracion        text,
    migraciones_aplicadas   integer,
    migraciones_posteriores integer
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, core
AS $$
    WITH vigente AS (
        SELECT sm.etiqueta_version, sm.archivo, sm.aplicado_en
          FROM core.schema_migrations sm
         WHERE sm.etiqueta_version IS NOT NULL
         ORDER BY sm.archivo DESC
         LIMIT 1
    )
    SELECT v.etiqueta_version,
           v.archivo,
           v.aplicado_en,
           (SELECT max(sm.archivo) FROM core.schema_migrations sm),
           (SELECT count(*)::integer FROM core.schema_migrations sm),
           (SELECT count(*)::integer FROM core.schema_migrations sm
             WHERE v.archivo IS NULL OR sm.archivo > v.archivo)
      FROM (SELECT 1) AS uno
      LEFT JOIN vigente v ON true;
$$;

COMMENT ON FUNCTION core.version_esquema() IS
    'Versión vigente del esquema según core.schema_migrations: etiqueta, migración que la fijó, última '
    'migración aplicada y cuántas van después de la etiqueta. La verifica el smoke post-deploy (KC-DB-12-1).';

REVOKE ALL ON FUNCTION core.version_esquema() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.version_esquema() TO kallicode_app, kallicode_worker;

-- ----------------------------------------------------------------------------
-- 4. Esta migración fija el modelo v2
-- ----------------------------------------------------------------------------
SELECT set_config('kc.etiqueta_version', 'v2.0.0', true);
