-- ============================================================================
-- KALLICODE · Modelo de datos operacional
-- 01_schema.sql — Esquemas, extensiones, tipos y tablas con constraints
-- PostgreSQL 16 (Azure Database for PostgreSQL Flexible Server) + pgvector
-- Nota Azure: habilitar extensiones en el servidor antes de ejecutar:
--   az postgres flexible-server parameter set --name azure.extensions \
--      --value vector,pg_trgm ...
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Extensiones
-- ----------------------------------------------------------------------------
-- Los hashes usan la función nativa sha256() (PostgreSQL ≥ 11): sin extensión.
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- búsqueda de texto en títulos
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector: embeddings

-- ----------------------------------------------------------------------------
-- Esquemas
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS core;    -- estado operacional
CREATE SCHEMA IF NOT EXISTS vec;     -- colecciones vectoriales (pgvector)
CREATE SCHEMA IF NOT EXISTS audit;   -- auditoría inmutable (append-only)

-- ----------------------------------------------------------------------------
-- Roles de aplicación (los usuarios de conexión se crean fuera de este script)
--   kallicode_app   : rol de la API y los workers (sujeto a RLS)
--   kallicode_admin : operaciones/migraciones (BYPASSRLS lo otorga el DBA)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_app') THEN
    CREATE ROLE kallicode_app NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_admin') THEN
    CREATE ROLE kallicode_admin NOLOGIN;
  END IF;
END $$;

-- ----------------------------------------------------------------------------
-- Función de contexto de tenant (usada por RLS y por defecto de columnas)
-- La API ejecuta al abrir cada transacción:
--   SET LOCAL app.tenant_id = '<org del JWT>';
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.current_tenant() RETURNS text
LANGUAGE sql STABLE AS
$$ SELECT current_setting('app.tenant_id', true) $$;

-- ============================================================================
-- CATÁLOGOS GLOBALES (sin tenant_id, sin RLS)
-- ============================================================================

-- Planes comerciales: catálogo global que parametriza la validación comercial.
CREATE TABLE core.plans (
    codigo            text PRIMARY KEY,                  -- starter | growth | enterprise
    nombre            text NOT NULL,
    lineas            integer,                           -- NULL = ilimitadas
    loc_mes           integer,                           -- NULL = a medida
    max_usuarios      integer,                           -- NULL = ilimitados
    storage_mb        integer,                           -- evidencia/adjuntos
    docs_mb           integer,                           -- documentación del grafo
    permite_help      boolean NOT NULL DEFAULT false,    -- Kallicode Help como fuente
    despliegues       text[]  NOT NULL DEFAULT '{cloud}',
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plans_lineas_chk  CHECK (lineas  IS NULL OR lineas  > 0),
    CONSTRAINT plans_loc_chk     CHECK (loc_mes IS NULL OR loc_mes > 0)
);

INSERT INTO core.plans (codigo, nombre, lineas, loc_mes, max_usuarios, storage_mb, docs_mb, permite_help, despliegues) VALUES
  ('starter',    'Starter',    1,    3000, 5,    5120,   200,  false, '{cloud}'),
  ('growth',     'Growth',     3,    8000, 25,   25600,  1024, true,  '{cloud,hibrido}'),
  ('enterprise', 'Enterprise', NULL, NULL, NULL, NULL,   NULL, true,  '{cloud,hibrido,onprem}');

-- ============================================================================
-- TENANTS Y SUSCRIPCIONES
-- ============================================================================

-- Organizaciones (tenants). Tabla raíz de la multi-tenencia.
CREATE TABLE core.organizations (
    id                text PRIMARY KEY,                  -- org_<slug|ulid>
    nombre            text NOT NULL,
    idioma            text NOT NULL DEFAULT 'es',
    timezone          text NOT NULL DEFAULT 'Europe/Madrid',
    despliegue        text NOT NULL DEFAULT 'cloud',
    fabrica_activa    boolean NOT NULL DEFAULT false,    -- onboarding completado
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    eliminado_en      timestamptz,                       -- soft-delete
    CONSTRAINT org_idioma_chk     CHECK (idioma IN ('es','en','pt')),
    CONSTRAINT org_despliegue_chk CHECK (despliegue IN ('cloud','hibrido','onprem'))
);

-- Suscripción vigente e histórica de cada tenant.
CREATE TABLE core.subscriptions (
    id                text PRIMARY KEY,                  -- sub_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    plan_codigo       text NOT NULL REFERENCES core.plans(codigo),
    estado            text NOT NULL DEFAULT 'activa',
    inicia_el         date NOT NULL,
    renueva_el        date NOT NULL,                     -- inicio del próximo ciclo
    finaliza_el       date,                              -- NULL = vigente
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sub_estado_chk CHECK (estado IN ('activa','periodo_gracia','suspendida','finalizada')),
    CONSTRAINT sub_fechas_chk CHECK (renueva_el > inicia_el)
);
-- Una sola suscripción vigente por tenant:
CREATE UNIQUE INDEX subscriptions_vigente_uq
    ON core.subscriptions (tenant_id) WHERE finaliza_el IS NULL;

-- ============================================================================
-- USUARIOS, INVITACIONES Y SESIONES
-- ============================================================================

CREATE TABLE core.users (
    id                text PRIMARY KEY,                  -- u_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    email             text NOT NULL,
    nombre            text NOT NULL,
    rol               text NOT NULL DEFAULT 'member',
    estado            text NOT NULL DEFAULT 'activo',
    idioma            text NOT NULL DEFAULT 'es',
    password_hash     text,                              -- Argon2id (NULL si invitado)
    notificaciones    jsonb NOT NULL DEFAULT '{"email":true,"portal":true}',
    ultimo_acceso     timestamptz,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_rol_chk    CHECK (rol IN ('owner','admin','architect','approver','member','viewer')),
    CONSTRAINT users_estado_chk CHECK (estado IN ('activo','invitado','desactivado')),
    CONSTRAINT users_email_uq   UNIQUE (tenant_id, email)
);

CREATE TABLE core.invitations (
    id                text PRIMARY KEY,                  -- inv_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    email             text NOT NULL,
    rol               text NOT NULL,
    token_hash        text NOT NULL UNIQUE,              -- SHA-256 del token enviado
    invitado_por      text NOT NULL REFERENCES core.users(id),
    estado            text NOT NULL DEFAULT 'enviada',
    expira_en         timestamptz NOT NULL,
    usado_en          timestamptz,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inv_rol_chk    CHECK (rol IN ('owner','admin','architect','approver','member','viewer')),
    CONSTRAINT inv_estado_chk CHECK (estado IN ('enviada','aceptada','expirada','revocada'))
);
-- Una invitación pendiente por email y tenant:
CREATE UNIQUE INDEX invitations_pendiente_uq
    ON core.invitations (tenant_id, email) WHERE estado = 'enviada';

CREATE TABLE core.refresh_tokens (
    id                text PRIMARY KEY,                  -- rt_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    user_id           text NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    token_hash        text NOT NULL UNIQUE,              -- SHA-256; nunca el token
    emitido_en        timestamptz NOT NULL DEFAULT now(),
    expira_en         timestamptz NOT NULL,
    usado_en          timestamptz,                       -- rotación: un solo uso
    revocado          boolean NOT NULL DEFAULT false,
    ip                inet,
    user_agent        text
);

CREATE TABLE core.password_reset_tokens (
    id                text PRIMARY KEY,                  -- pr_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    user_id           text NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    token_hash        text NOT NULL UNIQUE,
    expira_en         timestamptz NOT NULL,
    usado_en          timestamptz,
    creado_en         timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================
-- LÍNEAS DE PRODUCCIÓN
-- ============================================================================

CREATE TABLE core.production_lines (
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    numero            integer NOT NULL,
    estado            text NOT NULL DEFAULT 'disponible',
    job_id            text,                              -- FK diferida: se añade tras crear jobs
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, numero),
    CONSTRAINT lines_estado_chk CHECK (estado IN ('disponible','ocupada','mantenimiento')),
    CONSTRAINT lines_coherencia_chk CHECK (
        (estado = 'ocupada' AND job_id IS NOT NULL) OR
        (estado <> 'ocupada' AND job_id IS NULL))
);

-- ============================================================================
-- TICKETS
-- ============================================================================

-- Contador del correlativo visible KC-#### por tenant (lo usa el trigger).
CREATE TABLE core.ticket_counters (
    tenant_id         text PRIMARY KEY REFERENCES core.organizations(id),
    ultimo            integer NOT NULL DEFAULT 1000
);

CREATE TABLE core.tickets (
    id                text PRIMARY KEY,                  -- tk_<ulid> (interno)
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    numero            text,                              -- KC-#### (lo asigna trigger)
    tipo              text NOT NULL,
    titulo            text NOT NULL,
    descripcion       text NOT NULL,
    prioridad         text NOT NULL DEFAULT 'media',     -- declarada (alta|media|baja)
    prioridad_pipeline text,                             -- P1..P4 (la fija Triage)
    etapa             text NOT NULL DEFAULT 'triage',
    gate_pendiente    smallint,                          -- 1|2|3 o NULL
    origen            text NOT NULL DEFAULT 'portal',
    origen_ref        jsonb,                             -- payload/clave del sistema origen
    reportado_por     text REFERENCES core.users(id),    -- NULL si origen externo
    usuario_final     jsonb,                             -- {id_externo, canal} de Kallicode Help
    modulo_pista      text,
    duplicado_de      text REFERENCES core.tickets(id),
    impacto           jsonb,                             -- {modulos[], tablas[], radio, riesgo_regresion}
    complejidad       text,
    pr_url            text,
    fecha_objetivo    date,
    motivo_cancelacion text,
    cancelado_por     text REFERENCES core.users(id),
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    cerrado_en        timestamptz,
    CONSTRAINT tk_tipo_chk   CHECK (tipo IN ('bug','mejora','funcionalidad','seguridad')),
    CONSTRAINT tk_prio_chk   CHECK (prioridad IN ('alta','media','baja')),
    CONSTRAINT tk_priop_chk  CHECK (prioridad_pipeline IS NULL OR prioridad_pipeline IN ('P1','P2','P3','P4')),
    CONSTRAINT tk_etapa_chk  CHECK (etapa IN ('triage','design','build','qa','security','deploy',
                                              'produccion','en_cola_por_cuota','cancelado','cerrado_duplicado')),
    CONSTRAINT tk_gate_chk   CHECK (gate_pendiente IS NULL OR gate_pendiente IN (1,2,3)),
    CONSTRAINT tk_origen_chk CHECK (origen IN ('portal','jira','github','kallicode_help','corestream')),
    CONSTRAINT tk_complej_chk CHECK (complejidad IS NULL OR complejidad IN ('trivial','small','medium','large')),
    CONSTRAINT tk_numero_uq  UNIQUE (tenant_id, numero),
    CONSTRAINT tk_cancel_chk CHECK (etapa <> 'cancelado' OR motivo_cancelacion IS NOT NULL),
    CONSTRAINT tk_dup_no_self_chk CHECK (duplicado_de IS NULL OR duplicado_de <> id)
);

CREATE TABLE core.ticket_attachments (
    id                text PRIMARY KEY,                  -- att_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text REFERENCES core.tickets(id) ON DELETE CASCADE,  -- NULL = pendiente de asociar
    nombre_archivo    text NOT NULL,
    content_type      text NOT NULL,
    tamano_bytes      bigint NOT NULL,
    blob_ref          text NOT NULL,                     -- ruta en Blob Storage
    clase             text NOT NULL DEFAULT 'usuario',   -- usuario | evidencia | documento
    escaneo           text NOT NULL DEFAULT 'pendiente',
    subido_por        text REFERENCES core.users(id),
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT att_tamano_chk  CHECK (tamano_bytes BETWEEN 1 AND 52428800),
    CONSTRAINT att_clase_chk   CHECK (clase IN ('usuario','evidencia','documento')),
    CONSTRAINT att_escaneo_chk CHECK (escaneo IN ('pendiente','limpio','infectado'))
);

CREATE TABLE core.ticket_watchers (
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    user_id           text NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticket_id, user_id)
);

-- ============================================================================
-- SPECS (Diseño) — versiones inmutables
-- ============================================================================

CREATE TABLE core.specs (
    id                text PRIMARY KEY,                  -- sp_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    version           integer NOT NULL,
    estado            text NOT NULL DEFAULT 'pendiente_gate1',
    spec_md_ref       text NOT NULL,                     -- narrativa en Blob
    scope_paths       text[] NOT NULL,
    plan_tests        jsonb,
    migraciones       jsonb,
    adr_borrador      jsonb,
    estimacion_loc    integer,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT spec_estado_chk CHECK (estado IN ('pendiente_gate1','aprobada','reemplazada','rechazada')),
    CONSTRAINT spec_version_uq UNIQUE (ticket_id, version),
    CONSTRAINT spec_scope_chk  CHECK (array_length(scope_paths, 1) >= 1)
);

CREATE TABLE core.spec_alternatives (
    spec_id           text NOT NULL REFERENCES core.specs(id) ON DELETE CASCADE,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    alt_id            text NOT NULL,                     -- 'A' | 'B' | 'C'
    titulo            text NOT NULL,
    descripcion       text NOT NULL,
    riesgo            text NOT NULL,
    recomendada       boolean NOT NULL DEFAULT false,
    elegida           boolean NOT NULL DEFAULT false,    -- la marca el Gate 1
    PRIMARY KEY (spec_id, alt_id),
    CONSTRAINT alt_riesgo_chk CHECK (riesgo IN ('bajo','medio','alto'))
);
-- Exactamente una recomendada por spec (parcial):
CREATE UNIQUE INDEX spec_alt_recomendada_uq
    ON core.spec_alternatives (spec_id) WHERE recomendada;
CREATE UNIQUE INDEX spec_alt_elegida_uq
    ON core.spec_alternatives (spec_id) WHERE elegida;

CREATE TABLE core.spec_criteria (
    spec_id           text NOT NULL REFERENCES core.specs(id) ON DELETE CASCADE,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    criterio_id       text NOT NULL,                     -- AC-1, AC-2...
    given_txt         text NOT NULL,
    when_txt          text NOT NULL,
    then_txt          text NOT NULL,
    PRIMARY KEY (spec_id, criterio_id),
    CONSTRAINT crit_id_chk CHECK (criterio_id ~ '^AC-[0-9]+$')
);

-- ============================================================================
-- JOBS (máquina de estados del pipeline)
-- ============================================================================

CREATE TABLE core.jobs (
    id                text PRIMARY KEY,                  -- job_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    estado            text NOT NULL DEFAULT 'pendiente_asignacion',
    linea             integer,
    prioridad         text NOT NULL,
    iteraciones       jsonb NOT NULL DEFAULT '{}',       -- {"design":2,"build":1,...}
    contexto          jsonb,                             -- payload de la última transición
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    terminado_en      timestamptz,
    CONSTRAINT job_estado_chk CHECK (estado IN (
        'pendiente_asignacion','triage','design','gate1','build','qa','gate2','security',
        'deploy_preflight','gate3','deploy','produccion',
        'pausado_por_cuota','escalado_humano','cancelado','cerrado_duplicado')),
    CONSTRAINT job_prio_chk CHECK (prioridad IN ('P1','P2','P3','P4')),
    CONSTRAINT job_linea_fk FOREIGN KEY (tenant_id, linea)
        REFERENCES core.production_lines(tenant_id, numero)
);
-- Un job activo por ticket:
CREATE UNIQUE INDEX jobs_activo_por_ticket_uq
    ON core.jobs (ticket_id)
    WHERE estado NOT IN ('produccion','cancelado','cerrado_duplicado');
-- Una línea no puede tener dos jobs activos:
CREATE UNIQUE INDEX jobs_linea_ocupada_uq
    ON core.jobs (tenant_id, linea)
    WHERE linea IS NOT NULL
      AND estado NOT IN ('produccion','cancelado','cerrado_duplicado');

-- FK diferida de production_lines.job_id → jobs.id
ALTER TABLE core.production_lines
    ADD CONSTRAINT lines_job_fk FOREIGN KEY (job_id) REFERENCES core.jobs(id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE core.job_transitions (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    job_id            text NOT NULL REFERENCES core.jobs(id) ON DELETE CASCADE,
    de_estado         text NOT NULL,
    a_estado          text NOT NULL,
    motivo            text,
    contexto          jsonb,
    actor             text NOT NULL DEFAULT 'sistema',   -- sistema | svc:<agente> | user:<id>
    creado_en         timestamptz NOT NULL DEFAULT now()
);

-- Pasos LLM de las cadenas de prompts.
CREATE TABLE core.llm_steps (
    id                text PRIMARY KEY,                  -- st_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    job_id            text NOT NULL REFERENCES core.jobs(id) ON DELETE CASCADE,
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    etapa             text NOT NULL,
    paso              text NOT NULL,                     -- p. ej. triage.classify
    version_plantilla text NOT NULL,
    modelo            text NOT NULL,
    tier              text NOT NULL,
    confianza         numeric(4,3),
    validacion        text NOT NULL,
    reintentos        smallint NOT NULL DEFAULT 0,
    tokens_in         integer NOT NULL,
    tokens_out        integer NOT NULL,
    duracion_ms       integer NOT NULL,
    entrada_ref       text NOT NULL,                     -- Blob
    salida_ref        text NOT NULL,                     -- Blob
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT step_etapa_chk  CHECK (etapa IN ('triage','design','build','qa','security','deploy')),
    CONSTRAINT step_tier_chk   CHECK (tier IN ('pro','flash','vision')),
    CONSTRAINT step_valid_chk  CHECK (validacion IN ('esquema_valido','reintento','escalado_tier','escalado_humano')),
    CONSTRAINT step_conf_chk   CHECK (confianza IS NULL OR (confianza >= 0 AND confianza <= 1)),
    CONSTRAINT step_tokens_chk CHECK (tokens_in >= 0 AND tokens_out >= 0 AND duracion_ms >= 0)
);

-- ============================================================================
-- GATES (firmas humanas)
-- ============================================================================

CREATE TABLE core.gate_signatures (
    id                text PRIMARY KEY,                  -- gs_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    gate              smallint NOT NULL,
    accion            text NOT NULL,                     -- aprobado | cambios_pedidos
    actor_id          text NOT NULL REFERENCES core.users(id),
    comentario        text,
    alternativa_id    text,                              -- Gate 1
    acepta_fallos     boolean NOT NULL DEFAULT false,    -- Gate 2
    devolver_a        text,                              -- Gate 3: design|build|qa
    spec_id           text REFERENCES core.specs(id),    -- versión firmada (Gate 1)
    sello             text NOT NULL,                     -- hash del evento de auditoría
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gate_num_chk    CHECK (gate IN (1,2,3)),
    CONSTRAINT gate_accion_chk CHECK (accion IN ('aprobado','cambios_pedidos')),
    CONSTRAINT gate_dev_chk    CHECK (devolver_a IS NULL OR devolver_a IN ('design','build','qa'))
);
-- Una única APROBACIÓN por gate y ticket (las devoluciones pueden repetirse):
CREATE UNIQUE INDEX gate_aprobado_uq
    ON core.gate_signatures (ticket_id, gate) WHERE accion = 'aprobado';

-- ============================================================================
-- QA (corridas y matriz de evidencia)
-- ============================================================================

CREATE TABLE core.qa_runs (
    id                text PRIMARY KEY,                  -- qr_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    spec_id           text NOT NULL REFERENCES core.specs(id),
    corrida           integer NOT NULL,
    regresion         jsonb NOT NULL,                    -- {dirigidos, dirigidos_ok, suite_cliente, cliente_ok}
    flaky             jsonb NOT NULL DEFAULT '[]',
    ejecutada_en      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qa_corrida_uq UNIQUE (ticket_id, corrida)
);

CREATE TABLE core.qa_results (
    qa_run_id         text NOT NULL REFERENCES core.qa_runs(id) ON DELETE CASCADE,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    criterio_id       text NOT NULL,                     -- AC-n de la spec
    resultado         text NOT NULL,
    extracto_fallo    text,
    veredicto_visual  jsonb,                             -- {veredicto, observado, region}
    evidencias        jsonb NOT NULL DEFAULT '[]',       -- [{tipo, blob_ref}]
    PRIMARY KEY (qa_run_id, criterio_id),
    CONSTRAINT qares_resultado_chk CHECK (resultado IN ('pasa','falla','flaky')),
    CONSTRAINT qares_fallo_chk CHECK (resultado <> 'falla' OR extracto_fallo IS NOT NULL)
);

-- ============================================================================
-- SECURITY (hallazgos)
-- ============================================================================

CREATE TABLE core.security_findings (
    id                text PRIMARY KEY,                  -- sf_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    origen            text NOT NULL,                     -- sast | sca | secrets
    regla             text,
    archivo           text,
    linea             integer,
    severidad         text NOT NULL,
    titulo            text NOT NULL,
    veredicto         text NOT NULL,
    justificacion     text,
    fix_diff_ref      text,                              -- Blob
    bloquea           boolean NOT NULL DEFAULT false,
    estado            text NOT NULL DEFAULT 'abierto',
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sf_origen_chk    CHECK (origen IN ('sast','sca','secrets')),
    CONSTRAINT sf_severidad_chk CHECK (severidad IN ('critica','alta','media','baja')),
    CONSTRAINT sf_veredicto_chk CHECK (veredicto IN ('confirmado','falso_positivo')),
    CONSTRAINT sf_estado_chk    CHECK (estado IN ('abierto','corregido','descartado')),
    -- Todo falso positivo o descarte exige justificación (compliance):
    CONSTRAINT sf_justif_chk CHECK (
        (veredicto = 'falso_positivo' OR estado = 'descartado') = (justificacion IS NOT NULL)
        OR (veredicto = 'confirmado' AND estado <> 'descartado'))
);

-- ============================================================================
-- CONSUMO (tokens y LOC) — validación comercial
-- ============================================================================

CREATE TABLE core.usage_tokens (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text NOT NULL REFERENCES core.tickets(id) ON DELETE CASCADE,
    etapa             text NOT NULL,
    modelo            text NOT NULL,
    tier              text NOT NULL,
    invocaciones      integer NOT NULL DEFAULT 1,
    tokens_in         integer NOT NULL,
    tokens_out        integer NOT NULL,
    duracion_ms       integer NOT NULL,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ut_pos_chk CHECK (tokens_in >= 0 AND tokens_out >= 0)
);

-- Único punto que descuenta cuota LOC (QU-1). Un registro por PR.
CREATE TABLE core.usage_loc (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text REFERENCES core.tickets(id) ON DELETE SET NULL,
    ciclo             text NOT NULL,                     -- 'YYYY-MM'
    tipo              text NOT NULL DEFAULT 'pr',        -- pr | ajuste
    pr_url            text,
    anadidas          integer NOT NULL DEFAULT 0,
    eliminadas        integer NOT NULL DEFAULT 0,
    netas             integer NOT NULL,
    tests             integer NOT NULL DEFAULT 0,
    motivo_ajuste     text,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT loc_ciclo_chk  CHECK (ciclo ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT loc_tipo_chk   CHECK (tipo IN ('pr','ajuste')),
    CONSTRAINT loc_netas_chk  CHECK (tipo <> 'pr' OR netas = anadidas - eliminadas),
    CONSTRAINT loc_pr_chk     CHECK (tipo <> 'pr' OR pr_url IS NOT NULL),
    CONSTRAINT loc_ajuste_chk CHECK (tipo <> 'ajuste' OR motivo_ajuste IS NOT NULL)
);
CREATE UNIQUE INDEX usage_loc_pr_uq ON core.usage_loc (pr_url) WHERE tipo = 'pr';

-- Agregado por ciclo (lo mantiene un trigger; el middleware comercial lo lee).
CREATE TABLE core.usage_cycles (
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ciclo             text NOT NULL,
    loc_consumidas    integer NOT NULL DEFAULT 0,
    umbral            text NOT NULL DEFAULT 'normal',    -- normal | aviso_80 | agotado
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, ciclo),
    CONSTRAINT uc_umbral_chk CHECK (umbral IN ('normal','aviso_80','agotado'))
);

-- ============================================================================
-- INTEGRACIONES
-- ============================================================================

CREATE TABLE core.connections (
    id                text PRIMARY KEY,                  -- cn_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    categoria         text NOT NULL,
    proveedor         text NOT NULL,
    nombre            text,
    config            jsonb NOT NULL DEFAULT '{}',
    secreto_ref       text,                              -- referencia en Key Vault (nunca el valor)
    ruta_token        text UNIQUE,                       -- token de ruta del webhook (wh_...)
    estado            text NOT NULL DEFAULT 'pendiente',
    ultimo_test       timestamptz,
    ultimo_test_detalle jsonb,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cn_categoria_chk CHECK (categoria IN ('repositorio','tickets','cicd','database')),
    CONSTRAINT cn_estado_chk    CHECK (estado IN ('conectada','pendiente','pendiente_oauth','error','deshabilitada')),
    CONSTRAINT cn_proveedor_chk CHECK (proveedor IN (
        'github','gitlab','bitbucket','corestream_repo',
        'jira','github_issues','kallicode_help','corestream',
        'github_actions','gitlab_ci','jenkins',
        'postgresql','mysql','sqlserver','oracle'))
);

-- Mapa de ramas: una fila por tenant.
CREATE TABLE core.branch_map (
    tenant_id         text PRIMARY KEY REFERENCES core.organizations(id),
    feature_pattern   text NOT NULL DEFAULT 'feature/*',
    staging_branch    text NOT NULL DEFAULT 'develop',
    prod_branch       text NOT NULL DEFAULT 'main',
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bm_distintas_chk CHECK (staging_branch <> prod_branch)
);

CREATE TABLE core.webhook_inbox (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    connection_id     text NOT NULL REFERENCES core.connections(id) ON DELETE CASCADE,
    delivery_id       text NOT NULL,
    evento            text,
    payload           jsonb NOT NULL,
    estado            text NOT NULL DEFAULT 'pendiente',
    error_detalle     text,
    ticket_id         text REFERENCES core.tickets(id),
    recibido_en       timestamptz NOT NULL DEFAULT now(),
    procesado_en      timestamptz,
    CONSTRAINT wi_estado_chk CHECK (estado IN ('pendiente','procesado','ignorado','fallido')),
    CONSTRAINT wi_delivery_uq UNIQUE (connection_id, delivery_id)
);

-- ============================================================================
-- MODELOS IA
-- ============================================================================

CREATE TABLE core.model_providers (
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    proveedor         text NOT NULL,
    llave_ref         text,                              -- Key Vault
    llave_mascara     text,                              -- ••••4Jx2
    base_url          text,                              -- self-hosted
    estado            text NOT NULL DEFAULT 'sin_configurar',
    modelos_disponibles jsonb NOT NULL DEFAULT '[]',
    ultimo_test       timestamptz,
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, proveedor),
    CONSTRAINT mp_proveedor_chk CHECK (proveedor IN ('anthropic','openai','google','deepseek_selfhosted')),
    CONSTRAINT mp_estado_chk    CHECK (estado IN ('valida','invalida','sin_configurar')),
    CONSTRAINT mp_selfhost_chk  CHECK (proveedor <> 'deepseek_selfhosted' OR base_url IS NOT NULL)
);

CREATE TABLE core.model_assignments (
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    funcion           text NOT NULL,
    principal_proveedor text NOT NULL,
    principal_modelo  text NOT NULL,
    fallback_proveedor text NOT NULL,
    fallback_modelo   text NOT NULL,
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, funcion),
    CONSTRAINT ma_funcion_chk CHECK (funcion IN ('triage','design','build','qa','security','deploy')),
    CONSTRAINT ma_distintos_chk CHECK (principal_proveedor <> fallback_proveedor
                                       OR principal_modelo <> fallback_modelo),
    CONSTRAINT ma_prov_ppal_fk FOREIGN KEY (tenant_id, principal_proveedor)
        REFERENCES core.model_providers(tenant_id, proveedor),
    CONSTRAINT ma_prov_fb_fk FOREIGN KEY (tenant_id, fallback_proveedor)
        REFERENCES core.model_providers(tenant_id, proveedor)
);

-- ============================================================================
-- SOPORTE OPERATIVO
-- ============================================================================

CREATE TABLE core.notifications (
    id                text PRIMARY KEY,                  -- nt_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    user_id           text NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    tipo              text NOT NULL,
    titulo            text NOT NULL,
    cuerpo            text,
    ticket_id         text REFERENCES core.tickets(id) ON DELETE CASCADE,
    leida             boolean NOT NULL DEFAULT false,
    creada_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nt_tipo_chk CHECK (tipo IN ('cambio_etapa','gate_pendiente','ticket_cerrado',
                                           'aviso_cuota','seguridad','sistema'))
);

CREATE TABLE core.idempotency_keys (
    clave             text NOT NULL,
    tenant_id         text NOT NULL,
    endpoint          text NOT NULL,
    respuesta         jsonb NOT NULL,
    status_code       smallint NOT NULL,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, endpoint, clave)
);

CREATE TABLE core.escalations (
    id                text PRIMARY KEY,                  -- esc_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    job_id            text NOT NULL REFERENCES core.jobs(id) ON DELETE CASCADE,
    etapa             text NOT NULL,
    paso              text,
    motivo            text NOT NULL,
    severidad         text NOT NULL,
    detalle           jsonb NOT NULL,
    estado            text NOT NULL DEFAULT 'abierta',
    resuelto_por      text,
    resuelto_en       timestamptz,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT esc_motivo_chk CHECK (motivo IN ('validacion_persistente','baja_confianza',
        'max_iteraciones','selector_repair','anomalia_deploy','otro')),
    CONSTRAINT esc_sev_chk    CHECK (severidad IN ('baja','media','alta','critica')),
    CONSTRAINT esc_estado_chk CHECK (estado IN ('abierta','en_curso','resuelta'))
);
-- Un escalado abierto por job y paso:
CREATE UNIQUE INDEX escalations_abierta_uq
    ON core.escalations (job_id, coalesce(paso, '-')) WHERE estado = 'abierta';

CREATE TABLE core.audit_exports (
    id                text PRIMARY KEY,                  -- exp_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    formato           text NOT NULL,
    ticket_id         text REFERENCES core.tickets(id),
    filtros           jsonb,
    estado            text NOT NULL DEFAULT 'encolada',
    blob_ref          text,
    solicitado_por    text NOT NULL REFERENCES core.users(id),
    creado_en         timestamptz NOT NULL DEFAULT now(),
    completado_en     timestamptz,
    CONSTRAINT exp_formato_chk CHECK (formato IN ('csv','pdf')),
    CONSTRAINT exp_estado_chk  CHECK (estado IN ('encolada','procesando','lista','fallida')),
    CONSTRAINT exp_pdf_chk     CHECK (formato <> 'pdf' OR ticket_id IS NOT NULL)
);

CREATE TABLE core.onboarding_documents (
    id                text PRIMARY KEY,                  -- doc_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    nombre_archivo    text NOT NULL,
    content_type      text NOT NULL,
    tamano_bytes      bigint NOT NULL,
    tipo_documento    text NOT NULL DEFAULT 'otro',
    blob_ref          text NOT NULL,
    hash_contenido    text,                              -- dedup por contenido
    estado            text NOT NULL DEFAULT 'pendiente',
    subido_por        text NOT NULL REFERENCES core.users(id),
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT od_tipo_chk   CHECK (tipo_documento IN ('manual','spec','adr','negocio','otro')),
    CONSTRAINT od_estado_chk CHECK (estado IN ('pendiente','procesado','fallido')),
    CONSTRAINT od_tamano_chk CHECK (tamano_bytes BETWEEN 1 AND 52428800)
);
CREATE UNIQUE INDEX onboarding_docs_hash_uq
    ON core.onboarding_documents (tenant_id, hash_contenido) WHERE hash_contenido IS NOT NULL;

CREATE TABLE core.upgrade_requests (
    id                text PRIMARY KEY,                  -- upg_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    plan_deseado      text NOT NULL REFERENCES core.plans(codigo),
    comentario        text,
    urgente           boolean NOT NULL DEFAULT false,
    estado            text NOT NULL DEFAULT 'registrada',
    solicitado_por    text NOT NULL REFERENCES core.users(id),
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT upg_estado_chk CHECK (estado IN ('registrada','en_gestion','aplicada','descartada'))
);
CREATE UNIQUE INDEX upgrade_abierta_uq
    ON core.upgrade_requests (tenant_id) WHERE estado IN ('registrada','en_gestion');

-- ============================================================================
-- AUDITORÍA INMUTABLE (esquema audit)
-- ============================================================================

-- Cabeza de cadena por tenant: serializa el cálculo del hash encadenado.
CREATE TABLE audit.chain_heads (
    tenant_id         text PRIMARY KEY REFERENCES core.organizations(id),
    ultimo_sello      text NOT NULL DEFAULT repeat('0', 64),  -- génesis
    eventos           bigint NOT NULL DEFAULT 0
);

-- Registro append-only. Sin UPDATE/DELETE (trigger + permisos).
CREATE TABLE audit.audit_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text,                              -- sin FK: la auditoría sobrevive a todo
    job_id            text,
    etapa             text,
    actor_tipo        text NOT NULL,                     -- humano | agente | sistema
    actor_id          text,                              -- user_id o nombre del agente
    evento            text NOT NULL,                     -- código estable (gate_firmado, spec_generada...)
    resumen           text NOT NULL,                     -- texto visible en el portal
    datos             jsonb,                             -- payload estructurado del evento
    modelo            text,                              -- si actor agente
    step_id           text,                              -- ref a core.llm_steps
    sello_previo      text NOT NULL,
    sello             text NOT NULL,                     -- SHA-256(sello_previo || canónico)
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ae_actor_chk CHECK (actor_tipo IN ('humano','agente','sistema')),
    CONSTRAINT ae_sello_chk CHECK (sello ~ '^[0-9a-f]{64}$')
);

-- ============================================================================
-- VECTORIAL (esquema vec, pgvector) — BGE-M3, 1024 dimensiones
-- ============================================================================

-- Embeddings de tickets: deduplicación vectorial de Triage y dedup-preview.
CREATE TABLE vec.ticket_embeddings (
    ticket_id         text PRIMARY KEY REFERENCES core.tickets(id) ON DELETE CASCADE,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    embedding         vector(1024) NOT NULL,
    modelo_embedding  text NOT NULL DEFAULT 'bge-m3',
    texto_hash        text NOT NULL,                     -- re-embeber solo si cambió
    creado_en         timestamptz NOT NULL DEFAULT now()
);

-- Chunks de documentación ingerida (onboarding y refresh diario del grafo).
CREATE TABLE vec.doc_chunks (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    documento_id      text NOT NULL REFERENCES core.onboarding_documents(id) ON DELETE CASCADE,
    chunk_idx         integer NOT NULL,
    contenido         text NOT NULL,
    embedding         vector(1024) NOT NULL,
    metadatos         jsonb NOT NULL DEFAULT '{}',       -- {seccion, pagina, tipo_documento}
    creado_en         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dc_chunk_uq UNIQUE (documento_id, chunk_idx)
);

-- Definiciones semánticas de CodeMapping (capa de definiciones del grafo):
-- respaldan impact-preview y la recuperación en dos fases de Diseño.
CREATE TABLE vec.definition_embeddings (
    id                text PRIMARY KEY,                  -- de_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    nodo_ref          text NOT NULL,                     -- id del nodo en el grafo (Neo4j)
    capa              text NOT NULL,                     -- definicion | negocio | codigo
    nombre            text NOT NULL,
    descripcion       text NOT NULL,
    modulo            text,
    embedding         vector(1024) NOT NULL,
    actualizado_en    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT de_capa_chk CHECK (capa IN ('definicion','negocio','codigo')),
    CONSTRAINT de_nodo_uq  UNIQUE (tenant_id, nodo_ref)
);

COMMIT;
     