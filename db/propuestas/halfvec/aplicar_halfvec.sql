-- ============================================================================
-- KALLICODE · db/propuestas/halfvec/aplicar_halfvec.sql
-- KC-DB-08-3 (DB-F31) — Conversión de las colecciones de vec.* de vector(1024) (float4) a halfvec(1024)
-- (float2). PREPARADA Y ENSAYADA, NO APLICADA: el ADR-022 decidió mantener float4 porque con el HNSW halfvec
-- pierde 4-5 puntos de recall@10 (criterio de adopción: < 1). Se ejecuta solo si se cumple el criterio de
-- reversión del ADR-022 (se repite packages/core/tests/bench_halfvec.py --tabla y halfvec queda por debajo
-- de un punto de pérdida en la colección, o la memoria obliga y el ADR lo acepta).
--
-- Requiere la migración 045_hnsw_opclase.sql. Uso (psql, como kallicode_admin, en una ventana: reescribe las
-- tablas bajo bloqueo exclusivo):
--     psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/propuestas/halfvec/aplicar_halfvec.sql
-- Revertir: db/propuestas/halfvec/revertir_halfvec.sql. Ensayo automático (con filas, dentro de una
-- transacción que se deshace): packages/core/tests/integracion/test_halfvec.py.
--
-- Qué hace:
--   1. Para cada colección del catálogo: retira el HNSW, convierte la columna (ALTER ... TYPE halfvec(1024);
--      en las particionadas se propaga a las particiones), actualiza vec.hnsw_catalogo.opclase y reconstruye
--      el HNSW con halfvec_cosine_ops. Si la colección tiene más de 100.000 filas la reconstrucción NO se
--      hace aquí (bloquearía las escrituras): se avisa y se ejecuta db/06_indices_concurrentes.sql;
--      entretanto las búsquedas funcionan, exactas y sin índice.
--   2. cm.busqueda_hibrida_sql() (043): los parámetros llegan como vector; se compara con $1::halfvec.
--   3. Código de la aplicación: cambiar CAST(:e AS vector) por CAST(:e AS halfvec) en los INSERT/consultas de
--      vectores.py, tickets.py (dedup-preview, impact-preview) y graph_loader.py (hay un cast implícito
--      vector -> halfvec, así que el código antiguo sigue funcionando, pero conviene hacerlo explícito).
--
-- Los vectores ya convertidos conservan solo la precisión de float2.
-- ============================================================================
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- 1. Conversión de las colecciones.
DO $$
DECLARE
    c record;
    filas bigint;
    tipo text;
BEGIN
    FOR c IN SELECT * FROM vec.hnsw_catalogo WHERE activo ORDER BY indice LOOP
        SELECT format_type(a.atttypid, a.atttypmod) INTO tipo
          FROM pg_attribute a
         WHERE a.attrelid = c.tabla::regclass AND a.attname = c.columna AND NOT a.attisdropped;
        IF tipo = 'halfvec(1024)' THEN
            -- Reejecución: solo se asegura que el catálogo diga la verdad.
            UPDATE vec.hnsw_catalogo SET opclase = 'halfvec_cosine_ops' WHERE indice = c.indice;
            CONTINUE;
        END IF;
        IF tipo <> 'vector(1024)' THEN
            RAISE EXCEPTION 'aplicar_halfvec: % tiene el tipo inesperado %', c.tabla, tipo;
        END IF;

        EXECUTE format('DROP INDEX IF EXISTS %s', split_part(c.tabla, '.', 1) || '.' || quote_ident(c.indice));
        EXECUTE format('ALTER TABLE %s ALTER COLUMN %I TYPE halfvec(1024) USING %I::halfvec(1024)',
                       c.tabla, c.columna, c.columna);
        UPDATE vec.hnsw_catalogo SET opclase = 'halfvec_cosine_ops' WHERE indice = c.indice;

        SELECT coalesce(sum(greatest(k.reltuples, 0)), 0)::bigint INTO filas
          FROM vec.hnsw_hojas(c.tabla::regclass) h JOIN pg_class k ON k.oid = h;
        IF filas <= 100000 THEN
            EXECUTE format('CREATE INDEX %I ON %s USING hnsw (%I halfvec_cosine_ops) WITH (m = %s, ef_construction = %s)',
                           c.indice, c.tabla, c.columna, c.m, c.ef_construction);
        ELSE
            RAISE NOTICE 'aplicar_halfvec: % tiene ~% filas: el HNSW % no se reconstruye en esta transacción; ejecute db/06_indices_concurrentes.sql (docs/runbook-vectorial.md).',
                c.tabla, filas, c.indice;
        END IF;
    END LOOP;
END $$;

-- 2. Consulta híbrida (043): comparar con el parámetro como halfvec. Se reescribe a partir de la
-- definición vigente para no duplicar 100 líneas de SQL; falla si no encuentra lo que espera.
DO $$
DECLARE
    def text;
    nueva text;
    n integer;
BEGIN
    SELECT pg_get_functiondef('cm.busqueda_hibrida_sql()'::regprocedure) INTO def;
    IF def LIKE '%<=> $1::halfvec%' THEN
        RETURN;   -- ya adaptada
    END IF;
    n := (length(def) - length(replace(def, '<=> $1', ''))) / length('<=> $1');
    IF n <> 6 THEN
        RAISE EXCEPTION 'aplicar_halfvec: cm.busqueda_hibrida_sql() debería contener 6 comparaciones "<=> $1" y tiene %', n;
    END IF;
    nueva := replace(def, '<=> $1', '<=> $1::halfvec');
    EXECUTE nueva;
END $$;
COMMIT;
