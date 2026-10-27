-- ============================================================================
-- KALLICODE · 002_esquema_cm.sql
-- KC-ONB-03-1 — Despliegue del esquema cm (CodeMapping) acordado en
-- KC-ONB-02-4: grafo de conocimiento del código en PostgreSQL 16, reemplaza
-- el diseño anterior sobre Neo4j (decisión D18). Ver docs/esquema_cm.md.
--
-- Aislamiento multi-tenant: tenant_id + RLS, igual que core/vec/audit. El
-- RLS de cm queda autocontenido al final de este archivo (no en
-- db/04_rls.sql, que corre antes que las migraciones en `make db`).
-- ============================================================================


CREATE SCHEMA IF NOT EXISTS cm;   -- grafo de conocimiento de CodeMapping

-- ----------------------------------------------------------------------------
-- Snapshots de ingesta: una fila por corrida (completa o refresco). El grafo
-- de un snapshot no se considera vigente hasta estado = 'completo'.
-- ----------------------------------------------------------------------------
CREATE TABLE cm.repo_snapshots (
    id             text NOT NULL,                 -- snap_<ulid>
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    commit_sha     text NOT NULL,
    rama           text NOT NULL,
    estado         text NOT NULL DEFAULT 'en_progreso',
    fase_actual    text,                           -- último punto de control confirmado
    stats          jsonb NOT NULL DEFAULT '{}',    -- recuentos por capa, duración por fase
    iniciado_en    timestamptz NOT NULL DEFAULT now(),
    finalizado_en  timestamptz,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT snapshots_estado_chk CHECK (estado IN ('en_progreso','completo','fallido','pendiente_intervencion'))
);

-- ----------------------------------------------------------------------------
-- Archivos, símbolos: capa determinista (fases 1-2 de CodeMapping)
-- ----------------------------------------------------------------------------
CREATE TABLE cm.files (
    id            text NOT NULL,                   -- file_<ulid determinista>
    tenant_id     text NOT NULL REFERENCES core.organizations(id),
    path          text NOT NULL,
    language      text NOT NULL,
    sha256        text NOT NULL,
    snapshot_id   text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, path),
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id)
);

CREATE TABLE cm.symbols (
    id           text NOT NULL,                    -- sha1(path:name:línea:col)
    tenant_id    text NOT NULL REFERENCES core.organizations(id),
    file_id      text NOT NULL,
    kind         text NOT NULL,
    name         text NOT NULL,
    start_line   integer NOT NULL,
    end_line     integer NOT NULL,
    signature    text NOT NULL,
    descripcion  text,                              -- la llena la fase semántica (LLM)
    role         text,                              -- domain | infra | glue
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES cm.files(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT symbols_kind_chk CHECK (kind IN ('function','method','class','interface','enum','type')),
    CONSTRAINT symbols_role_chk CHECK (role IS NULL OR role IN ('domain','infra','glue'))
);

-- ----------------------------------------------------------------------------
-- Esquema de la base del cliente: capa determinista (fase 3)
-- ----------------------------------------------------------------------------
CREATE TABLE cm.db_tables (
    id        text NOT NULL,                        -- dbt_<ulid determinista>
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    esquema   text NOT NULL,
    nombre    text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, esquema, nombre)
);

CREATE TABLE cm.db_columns (
    id        text NOT NULL,                        -- dbc_<ulid determinista>
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    table_id  text NOT NULL,
    nombre    text NOT NULL,
    tipo      text NOT NULL,
    nullable  boolean NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, table_id, nombre),
    FOREIGN KEY (tenant_id, table_id) REFERENCES cm.db_tables(tenant_id, id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- Capa semántica: definiciones técnicas y conceptos de negocio (fase 4, LLM)
-- ----------------------------------------------------------------------------
CREATE TABLE cm.definitions (
    id          text NOT NULL,                      -- def_<ulid determinista>
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    modulo      text NOT NULL,
    nombre      text NOT NULL,
    kind        text NOT NULL,
    descripcion text NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE cm.concepts (
    id          text NOT NULL,                      -- con_<ulid determinista>
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    name_en     text NOT NULL,
    name_local  text NOT NULL,
    descripcion text NOT NULL,
    confianza   numeric(4,3),
    curado_por  text REFERENCES core.users(id),
    curado_en   timestamptz,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, name_en),
    CONSTRAINT concepts_conf_chk CHECK (confianza IS NULL OR (confianza >= 0 AND confianza <= 1))
);

-- ----------------------------------------------------------------------------
-- Aristas: toda relación del grafo en una sola tabla genérica. src_kind y
-- dst_kind identifican a qué tabla pertenece el nodo (file | symbol |
-- db_table | db_column | definition | concept); sin FK cruzada porque el
-- destino varía según el tipo.
-- ----------------------------------------------------------------------------
CREATE TABLE cm.edges (
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    tipo      text NOT NULL,
    src_kind  text NOT NULL,
    src_id    text NOT NULL,
    dst_kind  text NOT NULL,
    dst_id    text NOT NULL,
    props     jsonb NOT NULL DEFAULT '{}',          -- ej. {"confidence": 0.9} en EXPRESSES
    PRIMARY KEY (tenant_id, tipo, src_id, dst_id),
    CONSTRAINT edges_tipo_chk CHECK (tipo IN
        ('DEFINES','CALLS','HAS_COLUMN','REFERENCES','USES_TABLE','REALIZED_BY','RELATES','EXPRESSES')),
    CONSTRAINT edges_kind_chk CHECK (
        src_kind IN ('file','symbol','db_table','db_column','definition','concept') AND
        dst_kind IN ('file','symbol','db_table','db_column','definition','concept'))
);

-- ----------------------------------------------------------------------------
-- Curación humana, nuggets e incidentes (épica ECM5 — sin consumidores en
-- este ticket, pero se crean ya para no repartir la migración del esquema)
-- ----------------------------------------------------------------------------
CREATE TABLE cm.curation_queue (
    id           text NOT NULL,                      -- cq_<ulid>
    tenant_id    text NOT NULL REFERENCES core.organizations(id),
    tipo         text NOT NULL,                       -- concepto | enlace | nugget
    payload      jsonb NOT NULL,
    estado       text NOT NULL DEFAULT 'pendiente',
    revisado_por text REFERENCES core.users(id),
    revisado_en  timestamptz,
    creado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT curation_estado_chk CHECK (estado IN ('pendiente','aprobado','rechazado'))
);

CREATE TABLE cm.nuggets (
    id            text NOT NULL,                     -- ng_<ulid>
    tenant_id     text NOT NULL REFERENCES core.organizations(id),
    regla         text NOT NULL,
    scope_refs    jsonb NOT NULL DEFAULT '[]',
    evidencia_ref text NOT NULL,
    estado        text NOT NULL DEFAULT 'propuesto',
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT nuggets_estado_chk CHECK (estado IN ('propuesto','aprobado','rechazado'))
);

CREATE TABLE cm.incidents (
    id                text NOT NULL,                 -- inc_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text REFERENCES core.tickets(id),
    resumen           text NOT NULL,
    causa             text,
    correccion        text,
    nodos_afectados   jsonb NOT NULL DEFAULT '[]',
    creado_en         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

-- ----------------------------------------------------------------------------
-- Permisos y RLS de cm (mismo patrón que db/04_rls.sql). Autocontenido en
-- esta migración: 04_rls.sql corre antes que db/migrations/ en `make db`
-- (ver Makefile), así que un GRANT sobre 'cm' ahí fallaría en una
-- instalación nueva por no existir todavía el esquema.
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA cm TO kallicode_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cm TO kallicode_app;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT schemaname, tablename
          FROM pg_tables
         WHERE schemaname = 'cm'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',  r.schemaname, r.tablename);
        EXECUTE format($sql$
            CREATE POLICY tenant_isolation ON %I.%I
            USING (tenant_id = core.current_tenant())
            WITH CHECK (tenant_id = core.current_tenant())
        $sql$, r.schemaname, r.tablename);
    END LOOP;
END $$;

