-- ============================================================================
-- KALLICODE · db/propuestas/halfvec/revertir_halfvec.sql
-- KC-DB-08-3 — Reversión de aplicar_halfvec.sql: halfvec(1024) -> vector(1024).
--
-- Cuándo: tras aplicar_halfvec.sql, si el recall@10 en producción cae más de lo previsto, o aparece un modelo de
-- embeddings cuyos valores no caben en float2 (rango +-65504).
-- Uso (psql, como kallicode_admin, en una ventana: reescribe las tablas bajo bloqueo exclusivo):
--     psql -h <host> -U kallicode_admin -d <base> -v ON_ERROR_STOP=1 -f db/propuestas/halfvec/revertir_halfvec.sql
-- Después, para colecciones grandes, db/06_indices_concurrentes.sql reconstruye los HNSW que este script
-- haya dejado sin construir (más de 100.000 filas).
--
-- Los vectores conservan los valores ya redondeados a float2: la conversión no recupera los bits de
-- float4. Para recuperarlos hay que volver a calcular los embeddings (o restaurar una copia anterior a la conversión).
-- Deshace la conversión; la migración 046_hnsw_opclase.sql (columna opclase) se queda.
-- ============================================================================
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

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
        IF tipo <> 'halfvec(1024)' THEN
            CONTINUE;
        END IF;
        EXECUTE format('DROP INDEX IF EXISTS %s', split_part(c.tabla, '.', 1) || '.' || quote_ident(c.indice));
        EXECUTE format('ALTER TABLE %s ALTER COLUMN %I TYPE vector(1024) USING %I::vector(1024)',
                       c.tabla, c.columna, c.columna);
        UPDATE vec.hnsw_catalogo SET opclase = 'vector_cosine_ops' WHERE indice = c.indice;
        SELECT coalesce(sum(greatest(k.reltuples, 0)), 0)::bigint INTO filas
          FROM vec.hnsw_hojas(c.tabla::regclass) h JOIN pg_class k ON k.oid = h;
        IF filas <= 100000 THEN
            EXECUTE format('CREATE INDEX %I ON %s USING hnsw (%I vector_cosine_ops) WITH (m = %s, ef_construction = %s)',
                           c.indice, c.tabla, c.columna, c.m, c.ef_construction);
        ELSE
            RAISE NOTICE 'revertir_halfvec: % tiene ~% filas: ejecute db/06_indices_concurrentes.sql para reconstruir %.',
                c.tabla, filas, c.indice;
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE def text;
BEGIN
    SELECT pg_get_functiondef('cm.busqueda_hibrida_sql()'::regprocedure) INTO def;
    EXECUTE replace(def, '<=> $1::halfvec', '<=> $1');
END $$;
COMMIT;
