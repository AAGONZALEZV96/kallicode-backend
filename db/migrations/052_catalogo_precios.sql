-- ============================================================================
-- Migración 052 — KC-DB-09-3 (5 SP): catálogo de precios vigentes (core.plan_prices)
--
-- core.plans parametriza límites (lineas, loc_mes, max_usuarios, storage_mb, docs_mb, ...) pero
-- no guarda importe, moneda ni historial de tarifas: el ciclo comercial de D13 no tenía sobre qué
-- facturar. core.plan_prices guarda, con vigencia temporal, el importe unitario por plan y moneda:
--   * concepto 'suscripcion'              → importe por ciclo mensual del plan (tier NULL);
--   * concepto 'tokens_in' / 'tokens_out' → importe por 1.000.000 de tokens del tier LLM
--                                           (flash | pro | fable, los de usage_tokens.tier).
-- Un cambio de tarifa cierra la vigente (vigente_hasta) e inserta la nueva: el precio con el que
-- se facturó un ciclo pasado nunca se reescribe.
--
-- Contenido:
--   btree_gist                    extensión que permite '=' sobre text dentro de un EXCLUDE gist.
--   core.plan_prices              catálogo global (sin tenant_id) con EXCLUDE anti-solape.
--   RLS                           activada y forzada, con una única política de SOLO LECTURA.
--   core.precio_vigente(...)      tarifa aplicable en una fecha (la de un ciclo o un consumo).
--
-- Notas:
--   * La tabla se crea VACÍA: las tarifas comerciales las carga kallicode_admin (no hay importes
--     en el código ni en la migración).
--   * vigente_hasta es EXCLUSIVO ('[)'): la tarifa que empieza el día X y la que acaba el día X
--     no se solapan. NULL = vigencia abierta.
--   * Azure: añadir btree_gist a azure.extensions del Flexible Server antes de aplicar.
--   * No destructiva.
-- ============================================================================

-- 1. btree_gist ----------------------------------------------------------------
-- Es una extensión "trusted" (PG13+): kallicode_admin la crea con su CREATE sobre la base
-- (00_roles.sql). Si el servidor no la ofrece, la migración se niega con un mensaje explícito.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'KC-DB-09-3: no se pudo crear la extension btree_gist (%). '
                    'En Azure, permitirla en azure.extensions del servidor.', SQLERRM;
END $$;

-- 2. Catálogo -------------------------------------------------------------------
CREATE TABLE core.plan_prices (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_codigo       text NOT NULL REFERENCES core.plans(codigo),
    concepto          text NOT NULL,                     -- suscripcion | tokens_in | tokens_out
    tier              text,                              -- NULL en suscripcion
    moneda            text NOT NULL,                     -- ISO 4217 (EUR, USD, ...)
    importe           numeric(14,6) NOT NULL,            -- por ciclo o por 1M tokens
    vigente_desde     date NOT NULL,
    vigente_hasta     date,                              -- exclusivo; NULL = abierta
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pp_concepto_chk CHECK (concepto IN ('suscripcion','tokens_in','tokens_out')),
    CONSTRAINT pp_tier_chk     CHECK (tier IS NULL OR tier IN ('flash','pro','fable')),
    -- La suscripción no lleva tier; los conceptos de tokens lo exigen.
    CONSTRAINT pp_coherencia_chk CHECK ((concepto = 'suscripcion') = (tier IS NULL)),
    CONSTRAINT pp_moneda_chk   CHECK (moneda ~ '^[A-Z]{3}$'),
    CONSTRAINT pp_importe_chk  CHECK (importe >= 0),
    CONSTRAINT pp_vigencia_chk CHECK (vigente_hasta IS NULL OR vigente_hasta > vigente_desde),
    -- Una sola tarifa vigente en cada fecha por plan, concepto, tier y moneda. El índice gist que
    -- respalda la restricción sirve también a core.precio_vigente (plan_codigo = ... AND rango @> fecha).
    CONSTRAINT pp_vigencia_excl EXCLUDE USING gist (
        plan_codigo                                  WITH =,
        concepto                                     WITH =,
        (coalesce(tier, ''))                         WITH =,
        moneda                                       WITH =,
        daterange(vigente_desde, vigente_hasta, '[)') WITH &&
    )
);

COMMENT ON TABLE core.plan_prices IS
    'KC-DB-09-3: tarifas por plan, concepto (suscripcion | tokens_in | tokens_out), tier LLM y '
    'moneda, con vigencia [vigente_desde, vigente_hasta). Catálogo global de solo lectura para la app.';
COMMENT ON COLUMN core.plan_prices.importe IS
    'suscripcion: importe por ciclo mensual del plan. tokens_in/tokens_out: importe por 1.000.000 '
    'de tokens del tier.';
COMMENT ON COLUMN core.plan_prices.vigente_hasta IS
    'Fin EXCLUSIVO de la vigencia; NULL = vigente sin fecha de fin.';

-- 3. Permisos y RLS -------------------------------------------------------------
-- ALTER DEFAULT PRIVILEGES (04_rls.sql) concede escritura a kallicode_app en toda tabla nueva
-- de core: se revoca, igual que en core.plans. Solo kallicode_admin (dueño) da de alta tarifas.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON core.plan_prices FROM kallicode_app;
GRANT SELECT ON core.plan_prices TO kallicode_app, kallicode_worker;

-- Catálogo global sin tenant_id: la política no filtra por tenant, pero RLS queda activa y
-- forzada para que ninguna escritura entre aunque alguien conceda INSERT/UPDATE/DELETE por error
-- (no hay política que la admita).
ALTER TABLE core.plan_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.plan_prices FORCE ROW LEVEL SECURITY;
CREATE POLICY plan_prices_solo_lectura ON core.plan_prices
    FOR SELECT
    USING (true);

-- 4. Tarifa vigente en una fecha ---------------------------------------------------
-- Devuelve el importe aplicable en p_fecha (la fecha de inicio de un ciclo o la de un consumo),
-- no el actual. NULL si no hay tarifa para esa combinación; el EXCLUDE garantiza que nunca hay
-- más de una. SECURITY INVOKER: lee con los permisos y la RLS de quien la llama.
CREATE OR REPLACE FUNCTION core.precio_vigente(
    p_plan     text,
    p_moneda   text,
    p_fecha    date,
    p_concepto text DEFAULT 'suscripcion',
    p_tier     text DEFAULT NULL
)
RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT pp.importe
      FROM core.plan_prices pp
     WHERE pp.plan_codigo = p_plan
       AND pp.concepto    = p_concepto
       AND coalesce(pp.tier, '') = coalesce(p_tier, '')
       AND pp.moneda      = p_moneda
       AND daterange(pp.vigente_desde, pp.vigente_hasta, '[)') @> p_fecha
$$;

COMMENT ON FUNCTION core.precio_vigente(text, text, date, text, text) IS
    'KC-DB-09-3: importe vigente en p_fecha para (plan, moneda, concepto, tier); NULL si no hay tarifa.';

REVOKE ALL ON FUNCTION core.precio_vigente(text, text, date, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.precio_vigente(text, text, date, text, text)
    TO kallicode_app, kallicode_worker;
