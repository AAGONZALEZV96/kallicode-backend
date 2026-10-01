-- ============================================================================
-- KALLICODE · 00_roles.sql — Roles de base de datos y su modelo de privilegios
-- KC-DB-01-5 (EDB2). Única fuente de los roles: CI, Makefile y entornos lo
-- ejecutan igual. Reejecutable: crea lo que falta y reajusta los atributos.
--
-- Rol               Lo usa                   RLS        Atributos
-- ----------------  -----------------------  ---------  -------------------------
-- kallicode_app     API (apps/api)           sujeto     LOGIN
-- kallicode_worker  workers (apps/workers)   sujeto     LOGIN (colas: KC-DB-02-2)
-- kallicode_admin   migraciones y soporte    BYPASSRLS  LOGIN, dueño del esquema
--
-- Cada rol es su propio usuario de login. Ninguno es superusuario ni puede
-- crear bases o roles. El ÚNICO rol con BYPASSRLS es kallicode_admin.
--
-- Ejecutar como superusuario, con las contraseñas como variables de psql
-- (nunca se escriben en este archivo):
--   psql -v ON_ERROR_STOP=1 -v pw_app=... -v pw_worker=... -v pw_admin=... \
--        -d <base> -f db/00_roles.sql
-- ============================================================================


\if :{?pw_app}
\else
  \echo 'ERROR: falta -v pw_app=<contraseña de kallicode_app>'
  SELECT 1/0 AS falta_pw_app;
\endif
\if :{?pw_worker}
\else
  \echo 'ERROR: falta -v pw_worker=<contraseña de kallicode_worker>'
  SELECT 1/0 AS falta_pw_worker;
\endif
\if :{?pw_admin}
\else
  \echo 'ERROR: falta -v pw_admin=<contraseña de kallicode_admin>'
  SELECT 1/0 AS falta_pw_admin;
\endif

BEGIN;

-- 1. Crear los roles que no existan (los roles son de todo el clúster).
SELECT format('CREATE ROLE %I', r)
  FROM unnest(ARRAY['kallicode_app', 'kallicode_worker', 'kallicode_admin']) AS r
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
\gexec

-- 2. Fijar SIEMPRE los atributos: converge aunque el rol existiera con otros
--    (01_schema.sql antiguo los creaba NOLOGIN; el Makefile, con LOGIN).
ALTER ROLE kallicode_app    LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_app';
ALTER ROLE kallicode_worker LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_worker';
ALTER ROLE kallicode_admin  LOGIN NOSUPERUSER BYPASSRLS   NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_admin';

-- 3. Privilegios sobre la base actual: todos conectan; solo el admin crea
--    esquemas (core, vec, audit, cm).
SELECT format('GRANT CONNECT ON DATABASE %I TO kallicode_app, kallicode_worker, kallicode_admin',
              current_database())
\gexec
SELECT format('GRANT CREATE ON DATABASE %I TO kallicode_admin', current_database())
\gexec

COMMIT;
