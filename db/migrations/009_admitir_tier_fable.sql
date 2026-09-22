-- Migración DDL: KC-FAB-03-1 (Cierres BE-F10, BE-F41)
-- Amplía la restricción CHECK de tier en core.llm_steps y core.usage_tokens para admitir 'fable'

-- 1. Actualizar core.llm_steps
ALTER TABLE core.llm_steps 
    DROP CONSTRAINT llm_steps_tier_check;

ALTER TABLE core.llm_steps 
    ADD CONSTRAINT llm_steps_tier_check 
    CHECK (tier IN ('flash', 'pro', 'fable'));

-- 2. Actualizar core.usage_tokens
ALTER TABLE core.usage_tokens 
    DROP CONSTRAINT usage_tokens_tier_check;

ALTER TABLE core.usage_tokens 
    ADD CONSTRAINT usage_tokens_tier_check 
    CHECK (tier IN ('flash', 'pro', 'fable'));