-- ============================================================================
-- KALLICODE · 00_roles.sql — Roles de base de datos y su modelo de privilegios
-- KC-DB-01-5 (EDB2). Única fuente de los roles: CI, Makefile y entornos lo
-- ejecutan igual. Reejecutable: crea lo que falta y reajusta los atributos.
--
-- Rol               Lo usa                   RLS        Atributos
-- ----------------  -----------------------  ---------  -------------------------
-- kallicode_app     API (apps/api)           sujeto     LOGIN
-- kallicode_worker  workers (apps/workers)   sujeto     LOGIN (+ política de colas)
-- kallicode_admin   migraciones y soporte    BYPASSRLS  LOGIN, dueño del esquema
--
-- Cada rol es su propio usuario de login. Ninguno es superusuario ni puede
-- crear bases o roles. El ÚNICO rol con BYPASSRLS es kallicode_admin.
--
-- kallicode_worker NO tiene BYPASSRLS: ve todos los tenants solo en las 5
-- tablas de cola (webhook_inbox, jobs, refresh_tokens, ticket_attachments,
-- idempotency_keys) gracias a la política worker_colas, y no tiene GRANT
-- sobre ninguna otra tabla. Ver db/migrations/010_rol_worker.sql (KC-DB-02-2).
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

-- Rol de login no privilegiado para las pruebas de aislamiento RLS.
SELECT format('CREATE ROLE %I LOGIN IN ROLE kallicode_app', 'kallicode_dev')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_dev')
\gexec

-- 2. Fijar SIEMPRE los atributos: converge aunque el rol existiera con otros
--    (01_schema.sql antiguo los creaba NOLOGIN; el Makefile, con LOGIN).
ALTER ROLE kallicode_app    LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_app';
ALTER ROLE kallicode_worker LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_worker';
ALTER ROLE kallicode_admin  LOGIN NOSUPERUSER BYPASSRLS   NOCREATEDB NOCREATEROLE
                            PASSWORD :'pw_admin';
ALTER ROLE kallicode_dev    LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                            INHERIT PASSWORD :'pw_app';
GRANT kallicode_app TO kallicode_dev;

-- 2b. Escaneo iterativo de pgvector por ROL (KC-DB-08-1, DB-F10). Un índice HNSW indexa a todos los
--     tenants y la política RLS descarta DESPUÉS los que no son del tenant: con un tenant pequeño, los
--     40 candidatos por defecto casi nunca contienen resultados suyos y la búsqueda devuelve 0 o pocas
--     filas. hnsw.iterative_scan = relaxed_order sigue recorriendo el índice hasta reunir los resultados
--     (acotado por hnsw.max_scan_tuples). Se fija por rol (vale para toda conexión del rol, también tras
--     un pooler) y no por sesión; ef_search y max_scan_tuples, que dependen del caso de uso, se fijan por
--     transacción con vec.configurar_busqueda(caso) (migración 038). Tiene que estar aquí: ALTER ROLE ... SET
--     de otro rol lo hace un superusuario, no kallicode_admin.
ALTER ROLE kallicode_app    SET hnsw.iterative_scan = 'relaxed_order';
ALTER ROLE kallicode_worker SET hnsw.iterative_scan = 'relaxed_order';
ALTER ROLE kallicode_dev    SET hnsw.iterative_scan = 'relaxed_order';

-- 2c. maintenance_work_mem >= 2 GB para el rol que construye y reindexa (KC-DB-08-2, §9.2). Con menos, un
--     HNSW de muchas filas no cabe en memoria y pgvector lo construye en disco (mucho más lento: lo
--     avisa con un NOTICE). Es el rol de las migraciones, de db/06_indices_concurrentes.sql y de los
--     trabajos de pg_cron, así que el valor efectivo es el mismo en los tres. No afecta a la aplicación.
ALTER ROLE kallicode_admin  SET maintenance_work_mem = '2GB';

-- 3. Privilegios sobre la base actual: todos conectan; solo el admin crea
--    esquemas (core, vec, audit, cm).
SELECT format('GRANT CONNECT ON DATABASE %I TO kallicode_app, kallicode_worker, kallicode_admin',
              current_database())
\gexec
SELECT format('GRANT CREATE ON DATABASE %I TO kallicode_admin', current_database())
\gexec

COMMIT;
