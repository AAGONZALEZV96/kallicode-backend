-- Migración DDL: KC-FAB-03-1 (Cierres BE-F10, BE-F41)
-- Amplía la restricción CHECK de tier en core.llm_steps y core.usage_tokens para admitir 'fable'
--
-- Nota (fusión kallicode-backend -> kallicode-backend-1): los nombres de
-- restricción originales (llm_steps_tier_check, usage_tokens_tier_check) no
-- existen en el esquema real -- core.llm_steps la nombra step_tier_chk, y
-- core.usage_tokens no tenía restricción de tier en absoluto. Corregido para
-- que la migración aplique contra el esquema real.

-- 1. Actualizar core.llm_steps
ALTER TABLE core.llm_steps
    DROP CONSTRAINT step_tier_chk;

ALTER TABLE core.llm_steps
    ADD CONSTRAINT step_tier_chk
    CHECK (tier IN ('flash', 'pro', 'fable'));

-- 2. Actualizar core.usage_tokens (no tenía restricción de tier previa)
ALTER TABLE core.usage_tokens
    ADD CONSTRAINT ut_tier_chk
    CHECK (tier IN ('flash', 'pro', 'fable'));