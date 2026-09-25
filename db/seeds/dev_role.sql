-- ============================================================================
-- KALLICODE · seeds/dev_role.sql — Rol de LOGIN sin BYPASSRLS para desarrollo
--
-- 04_rls.sql define kallicode_app y kallicode_admin como roles "Cannot login"
-- (roles de permisos, para heredar), pero ningún .sql crea un usuario de login
-- que los herede. En la práctica, todo el mundo termina conectando como
-- "postgres" (superusuario, Bypass RLS) — con lo que las políticas de RLS
-- JAMÁS se ejercitan en local, ni siquiera en tests que creen probarlas.
--
-- Este seed (solo para desarrollo local, no usar en prod) crea un rol de
-- login real que hereda kallicode_app, para que sea posible conectar sin
-- privilegios de superusuario y así confirmar que el aislamiento por tenant
-- funciona de verdad. Idempotente: se puede correr más de una vez.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kallicode_dev') THEN
        CREATE ROLE kallicode_dev LOGIN PASSWORD 'dev' IN ROLE kallicode_app;
    END IF;
END
$$;