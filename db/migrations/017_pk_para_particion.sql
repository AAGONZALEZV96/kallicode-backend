-- Migración 017: Rediseño de PKs para habilitar particionado por rango temporal (KC-DB-03-5)
-- Cierra hallazgo DB-F09: Las tablas de crecimiento ilimitado requieren que la clave
-- de partición forme parte de la clave primaria.

SET lock_timeout = '5s';
SET statement_timeout = '30s';



-- 1. audit.audit_events -> PK (creado_en, id)
ALTER TABLE audit.audit_events DROP CONSTRAINT IF EXISTS audit_events_pkey;
ALTER TABLE audit.audit_events ADD CONSTRAINT audit_events_pkey PRIMARY KEY (creado_en, id);

-- 2. core.usage_tokens -> PK (creado_en, id)
ALTER TABLE core.usage_tokens DROP CONSTRAINT IF EXISTS usage_tokens_pkey;
ALTER TABLE core.usage_tokens ADD CONSTRAINT usage_tokens_pkey PRIMARY KEY (creado_en, id);

-- 3. core.job_transitions -> PK (creado_en, id)
ALTER TABLE core.job_transitions DROP CONSTRAINT IF EXISTS job_transitions_pkey;
ALTER TABLE core.job_transitions ADD CONSTRAINT job_transitions_pkey PRIMARY KEY (creado_en, id);

-- 4. core.webhook_inbox -> PK (recibido_en, id)
ALTER TABLE core.webhook_inbox DROP CONSTRAINT IF EXISTS webhook_inbox_pkey;
ALTER TABLE core.webhook_inbox ADD CONSTRAINT webhook_inbox_pkey PRIMARY KEY (recibido_en, id);

-- 5. core.notifications -> PK (creada_en, id)
ALTER TABLE core.notifications DROP CONSTRAINT IF EXISTS notifications_pkey;
ALTER TABLE core.notifications ADD CONSTRAINT notifications_pkey PRIMARY KEY (creada_en, id);

