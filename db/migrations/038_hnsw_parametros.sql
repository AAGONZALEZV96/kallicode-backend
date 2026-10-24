-- ============================================================================
-- KALLICODE · 038_hnsw_parametros.sql
-- KC-DB-08-1 (DB-F10, ALTO) — iterative_scan y presupuesto de ef_search por caso de uso.
--
-- El §9.2 fijaba hnsw.ef_search = 40 para la deduplicación y 100 para la recuperación de Diseño,
-- cifras elegidas SIN la política RLS en la ecuación. Un índice HNSW indexa a TODOS los tenants y la
-- política (tenant_id = core.current_tenant()) descarta después los que no son del tenant, de modo que
-- el número de candidatos necesarios depende de la fracción del corpus que representa el tenant. Con 40
-- candidatos y sin escaneo iterativo, un tenant del 1 % recupera el 63 % de sus duplicados (y con
-- densidades de vecinos ajenos mayores, nada: 0 filas) (packages/core/tests/bench_recall_vectorial.py, docs/plan-indices.md «Parámetros de búsqueda vectorial»).
--
--   * hnsw.iterative_scan = relaxed_order (pgvector 0.8): sigue recorriendo el índice hasta reunir los
--     resultados. Se fija POR ROL (ALTER ROLE ... SET en db/00_roles.sql, porque lo hace un superusuario)
--     y, por si el rol no lo trae, también por transacción en vec.configurar_busqueda().
--   * hnsw.ef_search y hnsw.max_scan_tuples dependen del caso de uso y se fijan POR TRANSACCIÓN, nunca
--     globalmente ni por sesión: una conexión de un pool la usan consultas de casos distintos. Esta
--     migración guarda la calibración en vec.parametros_busqueda y expone vec.configurar_busqueda(caso),
--     que hace el equivalente a SET LOCAL (set_config(..., true)).
--
-- Uso (Backend), DENTRO de la transacción de la consulta:
--     SELECT vec.configurar_busqueda('dedup');   -- o 'diseno'
--     SELECT ... FROM vec.ticket_embeddings ... ORDER BY embedding <=> :q LIMIT :k;
-- relaxed_order puede devolver los resultados ligeramente desordenados: reordenar fuera
-- (WITH r AS MATERIALIZED (... LIMIT k) SELECT * FROM r ORDER BY distancia + 0).
--
-- Nota: el ticket nombra esta migración 029_hnsw_parametros.sql; la 029 ya existe. Es la 038.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE TABLE IF NOT EXISTS vec.parametros_busqueda (
    caso             text PRIMARY KEY,
    ef_search        integer NOT NULL,
    max_scan_tuples  integer NOT NULL,
    recall_objetivo  numeric(4, 3) NOT NULL,
    metrica          text NOT NULL,
    justificacion    text NOT NULL,
    CONSTRAINT pb_caso_chk CHECK (caso IN ('dedup', 'diseno')),
    -- hnsw.ef_search admite de 1 a 1000 en pgvector; max_scan_tuples es un número de tuplas.
    CONSTRAINT pb_ef_chk CHECK (ef_search BETWEEN 1 AND 1000),
    CONSTRAINT pb_scan_chk CHECK (max_scan_tuples BETWEEN 1 AND 1000000),
    CONSTRAINT pb_recall_chk CHECK (recall_objetivo > 0 AND recall_objetivo <= 1)
);

COMMENT ON TABLE vec.parametros_busqueda IS
'KC-DB-08-1: parámetros de la búsqueda vectorial por caso de uso, calibrados con tests/bench_recall_vectorial.py (docs/plan-indices.md). Global (no por tenant). Se aplican con vec.configurar_busqueda(caso) dentro de la transacción de la consulta.';

-- Valores de la calibración (reproducible con: python packages/core/tests/bench_recall_vectorial.py --calibrar).
INSERT INTO vec.parametros_busqueda (caso, ef_search, max_scan_tuples, recall_objetivo, metrica, justificacion) VALUES
    ('dedup', 600, 5000, 0.980, 'recall de duplicados (coseno >= 0,70, hasta 10) para tenants del 1 %, 5 % y 50 % del corpus',
     'Menor ef_search que cumple recall >= 0,98 con margen para el tenant del 1 %: 400 da 0,98 y 0,98-1,00 según el grafo que salga al construir el índice (queda en el umbral y haría fallar el benchmark al azar); 600 da 1,00. Con 40 (el valor del §9.2) y sin escaneo iterativo el tenant del 1 % recupera el 62 %. max_scan_tuples 5000: con 1000 el 1 % pierde recall (0,86 a ef 40) y con 20000 no mejora (el escaneo se detiene al reunir el LIMIT).'),
    ('diseno', 400, 5000, 0.900, 'recall@10 frente al top-10 exacto para tenants del 1 %, 5 % y 50 % del corpus',
     'La recuperación de Diseño pide el ranking (recall@10 exacto), no solo los duplicados, pero tolera perder algún vecino: umbral 0,90. Con 100 (el valor del §9.2) el tenant del 1 % da 0,89, bajo el umbral; con 400 da 0,94 (margen 0,04); 600 da 0,96-0,97 a ~25 ms más. Es un paso de pipeline, no una búsqueda interactiva.')
ON CONFLICT (caso) DO NOTHING;

-- La aplicación y el worker leen los parámetros; no los cambian (los permisos por defecto de vec les dan
-- escritura sobre toda tabla nueva).
REVOKE ALL ON vec.parametros_busqueda FROM PUBLIC, kallicode_app, kallicode_worker;
GRANT SELECT ON vec.parametros_busqueda TO kallicode_app, kallicode_worker;

CREATE OR REPLACE FUNCTION vec.configurar_busqueda(p_caso text) RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    p vec.parametros_busqueda%ROWTYPE;
BEGIN
    SELECT * INTO p FROM vec.parametros_busqueda WHERE caso = p_caso;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'vec.configurar_busqueda: caso desconocido (%); casos: dedup, diseno', p_caso
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- is_local = true: equivale a SET LOCAL (vale hasta el final de la transacción actual, nunca para la sesión).
    PERFORM set_config('hnsw.iterative_scan', 'relaxed_order', true);
    PERFORM set_config('hnsw.ef_search', p.ef_search::text, true);
    PERFORM set_config('hnsw.max_scan_tuples', p.max_scan_tuples::text, true);
END $$;

COMMENT ON FUNCTION vec.configurar_busqueda(text) IS
'KC-DB-08-1: fija hnsw.iterative_scan, hnsw.ef_search y hnsw.max_scan_tuples para el caso de uso (dedup | diseno) con alcance de transacción (SET LOCAL). Llamar DENTRO de la transacción de la consulta, antes de ORDER BY embedding <=> ... LIMIT k.';
REVOKE ALL ON FUNCTION vec.configurar_busqueda(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vec.configurar_busqueda(text) TO kallicode_app, kallicode_worker;
