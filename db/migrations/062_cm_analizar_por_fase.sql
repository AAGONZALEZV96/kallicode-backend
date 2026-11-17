-- ============================================================================
-- KALLICODE · 062_cm_analizar_por_fase.sql
-- KC-CM-11-1 — ANALYZE por fase de la carga masiva del grafo.
--
-- Una carga masiva deja cm.symbols, cm.edges y compañía sin estadísticas hasta que el autovacuum las
-- recalcula (pasado su umbral y su siguiente ronda: minutos, no segundos). Todo lo que consulta esas
-- tablas justo después de la carga planifica a ciegas. Medido con el repositorio sintético de
-- KC-CM-11-1 (20.000 nodos, 26.000 aristas), el mismo cm.refrescar_reachability():
--
--     con estadísticas        1,5 s
--     sin estadísticas       63 s      (x42; crece más que linealmente: a 200.000 nodos son horas)
--
-- El cargador debe analizar al terminar cada fase, pero kallicode_app no es dueño de cm.* y en
-- PostgreSQL 16 solo el dueño (o un superusuario) puede ejecutar ANALYZE: sin esta función se omitiría
-- con un aviso. cm.analizar_tablas() corre como kallicode_admin (dueño del esquema), solo sobre una
-- lista blanca de tablas del grafo y sin parámetros que lleguen a SQL sin validar.
--
-- ANALYZE toma SHARE UPDATE EXCLUSIVE: no bloquea lecturas ni escrituras de otros tenants. Muestrea
-- (30.000 filas con el objetivo por defecto), así que su costo no crece con el tamaño de la tabla.
--
-- Aditiva y re-ejecutable. Sin índices nuevos. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE OR REPLACE FUNCTION cm.analizar_tablas(p_tablas text[] DEFAULT NULL)
RETURNS TABLE (tabla text, duracion_ms integer)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    -- Tablas del grafo que escribe la carga masiva (y la derivada que se lee justo después).
    v_permitidas constant text[] := ARRAY[
        'files', 'symbols', 'db_tables', 'db_columns', 'definitions', 'concepts', 'edges',
        'reachability'];
    v_pedidas    text[] := COALESCE(p_tablas, v_permitidas);
    v_t          text;
    v_inicio     timestamptz;
BEGIN
    FOREACH v_t IN ARRAY v_pedidas LOOP
        IF v_t IS NULL OR NOT (v_t = ANY (v_permitidas)) THEN
            RAISE EXCEPTION 'cm.analizar_tablas: tabla no permitida (%): solo %', v_t, v_permitidas
                USING ERRCODE = '22023';
        END IF;
    END LOOP;

    FOREACH v_t IN ARRAY v_pedidas LOOP
        v_inicio := clock_timestamp();
        EXECUTE format('ANALYZE cm.%I', v_t);
        tabla := v_t;
        duracion_ms := (extract(epoch FROM clock_timestamp() - v_inicio) * 1000)::integer;
        RETURN NEXT;
    END LOOP;
END
$f$;

COMMENT ON FUNCTION cm.analizar_tablas(text[]) IS
'KC-CM-11-1: ANALYZE de las tablas del grafo indicadas (o todas) tras una carga masiva. SECURITY DEFINER porque ANALYZE exige ser dueño; solo acepta tablas de la lista blanca. Devuelve la duración de cada una.';
REVOKE ALL ON FUNCTION cm.analizar_tablas(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm.analizar_tablas(text[])
    TO kallicode_app, kallicode_worker, kallicode_admin;
