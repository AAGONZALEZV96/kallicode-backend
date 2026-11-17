-- ============================================================================
-- Migración 049 — KC-DB-09-5 (3 SP): Tablas de dominio para roles de usuario
-- y etapas del pipeline con claves foráneas hacia tablas transaccionales.
-- ============================================================================


-- 1. Catálogo de dominio: core.user_roles
CREATE TABLE IF NOT EXISTS core.user_roles (
    codigo          text PRIMARY KEY,
    nombre          text NOT NULL,
    descripcion     text NOT NULL,
    orden_jerarquia smallint NOT NULL,
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO core.user_roles (codigo, nombre, descripcion, orden_jerarquia)
VALUES
    ('owner',     'Propietario', 'Control total de organización, facturación y supresión', 1),
    ('admin',     'Administrador', 'Gestión de usuarios, conexiones y configuración', 2),
    ('architect', 'Arquitecto', 'Revisión técnica y aprobación de diseño (Gate 1)', 3),
    ('approver',  'Aprobador', 'Firma de gates de calidad y despliegue (Gates 2 y 3)', 4),
    ('member',    'Miembro', 'Creación y seguimiento operativo de tickets', 5),
    ('viewer',    'Lector', 'Acceso de solo lectura al portal y auditoría', 6)
ON CONFLICT (codigo) DO NOTHING;

-- 2. Catálogo de dominio: core.pipeline_stages
CREATE TABLE IF NOT EXISTS core.pipeline_stages (
    codigo          text PRIMARY KEY,
    nombre          text NOT NULL,
    es_etapa_llm    boolean NOT NULL DEFAULT false,
    es_terminal     boolean NOT NULL DEFAULT false,
    orden_flujo     smallint NOT NULL,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO core.pipeline_stages (codigo, nombre, es_etapa_llm, es_terminal, orden_flujo)
VALUES
    ('triage',            'Triage',             true,  false, 10),
    ('design',            'Diseño',             true,  false, 20),
    ('build',             'Construcción',       true,  false, 30),
    ('qa',                'QA y Pruebas',       true,  false, 40),
    ('security',          'Seguridad',          true,  false, 50),
    ('deploy',            'Despliegue',         true,  false, 60),
    ('produccion',        'En Producción',      false, true,  70),
    ('en_cola_por_cuota', 'En Cola por Cuota',  false, false, 80),
    ('cancelado',         'Cancelado',          false, true,  90),
    ('cerrado_duplicado', 'Cerrado Duplicado',  false, true,  95)
ON CONFLICT (codigo) DO NOTHING;

-- 3. Permisos de lectura para roles operativos (catálogos globales)
GRANT SELECT ON core.user_roles, core.pipeline_stages TO kallicode_app, kallicode_worker, kallicode_admin;

-- 4. Sustituir CHECK constraints dispersos por Foreign Keys referenciales (idempotente)
ALTER TABLE core.users DROP CONSTRAINT IF EXISTS users_rol_chk;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_rol_fkey') THEN
        ALTER TABLE core.users
            ADD CONSTRAINT users_rol_fkey
            FOREIGN KEY (rol) REFERENCES core.user_roles(codigo);
    END IF;
END $$;

ALTER TABLE core.invitations DROP CONSTRAINT IF EXISTS inv_rol_chk;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'invitations_rol_fkey') THEN
        ALTER TABLE core.invitations
            ADD CONSTRAINT invitations_rol_fkey
            FOREIGN KEY (rol) REFERENCES core.user_roles(codigo);
    END IF;
END $$;

ALTER TABLE core.tickets DROP CONSTRAINT IF EXISTS tk_etapa_chk;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tickets_etapa_fkey') THEN
        ALTER TABLE core.tickets
            ADD CONSTRAINT tickets_etapa_fkey
            FOREIGN KEY (etapa) REFERENCES core.pipeline_stages(codigo);
    END IF;
END $$;

ALTER TABLE core.llm_steps DROP CONSTRAINT IF EXISTS step_etapa_chk;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'llm_steps_etapa_fkey') THEN
        ALTER TABLE core.llm_steps
            ADD CONSTRAINT llm_steps_etapa_fkey
            FOREIGN KEY (etapa) REFERENCES core.pipeline_stages(codigo);
    END IF;
END $$;
