-- ============================================================================
-- KALLICODE · 044_hnsw_operaciones.sql
-- KC-DB-08-2 (DB-F10, ALTO) — Construcción y reindexado concurrentes de HNSW como procedimiento
-- operativo.
--
-- El §9.2 exige construir los HNSW con CONCURRENTLY en producción y maintenance_work_mem >= 2 GB,
-- pero no había procedimiento. Esta migración deja lo que el procedimiento necesita en la base; el
-- procedimiento mismo es db/06_indices_concurrentes.sql (psql, fuera de transacción) y su runbook,
-- docs/runbook-vectorial.md.
--
--   * vec.hnsw_catalogo   los tres índices HNSW (tabla, columna, m, ef_construction): fuente única
--                         para el script de construcción y para las métricas.
--   * vec.hnsw_metricas   registro de cada construcción y de cada medición antes/después de un
--                         REINDEX: tamaño, filas vivas y muertas, bytes por fila, hinchazón,
--                         duración y maintenance_work_mem efectivo.
--   * vec.medir_hnsw()    inserta una fila por índice (suma las particiones de los particionados).
--   * vec.v_hnsw_estado   por índice: válido o no (una construcción concurrente interrumpida deja un
--                         índice INVÁLIDO que ocupa espacio y se mantiene en cada escritura) y cuántas
--                         particiones le faltan.
--   * pg_cron             REINDEX CONCURRENTLY semanal, un trabajo por índice, con medición antes y
--                         después (solo si pg_cron está instalada; si no, la migración lo avisa).
--
-- Hinchazón de un HNSW: pgstattuple no soporta el método de acceso hnsw, así que la hinchazón es un
-- INDICADOR: bytes de índice por fila viva frente al mejor valor registrado tras una construcción o un
-- REINDEX. Crece cuando UPDATE/DELETE dejan tuplas muertas en el grafo (se limpian con VACUUM, pero el
-- grafo no se compacta: solo un REINDEX lo reconstruye).
--
-- Nota: el ticket nombra los archivos 06_indices_concurrentes.sql, runbook-vectorial.md y
-- test_hnsw_concurrente.py; la migración es la 044 porque las anteriores están ocupadas.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE TABLE IF NOT EXISTS vec.hnsw_catalogo (
    indice           text PRIMARY KEY,
    tabla            text NOT NULL,               -- esquema.tabla, particionada o no
    columna          text NOT NULL DEFAULT 'embedding',
    m                integer NOT NULL DEFAULT 16,
    ef_construction  integer NOT NULL DEFAULT 64,
    activo           boolean NOT NULL DEFAULT true,
    CONSTRAINT hc_m_chk  CHECK (m BETWEEN 2 AND 100),
    CONSTRAINT hc_ef_chk CHECK (ef_construction BETWEEN 4 AND 1000)
);
COMMENT ON TABLE vec.hnsw_catalogo IS
'KC-DB-08-2: índices HNSW de vec.*; los lee db/06_indices_concurrentes.sql y vec.medir_hnsw(). m y ef_construction deben coincidir con db/02_indexes.sql.';

INSERT INTO vec.hnsw_catalogo (indice, tabla) VALUES
    ('ticket_embeddings_hnsw_ix',     'vec.ticket_embeddings'),
    ('doc_chunks_hnsw_ix',            'vec.doc_chunks'),
    ('definition_embeddings_hnsw_ix', 'vec.definition_embeddings')
ON CONFLICT (indice) DO NOTHING;

CREATE TABLE IF NOT EXISTS vec.hnsw_metricas (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    creado_en               timestamptz NOT NULL DEFAULT now(),
    indice                  text NOT NULL,
    evento                  text NOT NULL,
    tamano_bytes            bigint,
    filas_vivas             bigint,
    filas_muertas           bigint,
    bytes_por_fila          numeric(12, 1),
    hinchazon_pct           numeric(7, 1),
    duracion_s              numeric(12, 1),
    maintenance_work_mem    text,
    max_parallel_workers    integer,
    particiones             integer,
    detalle                 text,
    CONSTRAINT hm_evento_chk CHECK (evento IN ('construccion', 'antes', 'despues'))
);
-- Sin índice propio: tres filas por semana y por índice; la consulta de la línea base (por índice) las recorre sin coste.
COMMENT ON TABLE vec.hnsw_metricas IS
'KC-DB-08-2: construcciones y mediciones antes/después de REINDEX de los HNSW. hinchazon_pct = bytes por fila viva frente al mejor valor registrado tras construcción o REINDEX (indicador: pgstattuple no soporta hnsw).';

-- La aplicación y el worker no tocan estas tablas (los permisos por defecto de vec les dan escritura).
REVOKE ALL ON vec.hnsw_catalogo, vec.hnsw_metricas FROM PUBLIC, kallicode_app, kallicode_worker;

-- Hojas de una tabla: sus particiones si está particionada, ella misma si no (pg_partition_tree no
-- devuelve nada para una tabla sin particionar).
CREATE OR REPLACE FUNCTION vec.hnsw_hojas(p_tabla regclass) RETURNS SETOF oid
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT relid FROM pg_partition_tree(p_tabla) WHERE isleaf
    UNION
    SELECT p_tabla::oid WHERE (SELECT relkind FROM pg_class WHERE oid = p_tabla) IN ('r', 'm')
$$;
REVOKE ALL ON FUNCTION vec.hnsw_hojas(regclass) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- Medición: una fila por índice activo del catálogo.
--   p_evento     'construccion' | 'antes' | 'despues'
--   p_duracion_s si se omite y el evento es 'despues', se toma de pg_cron (cron.job_run_details) el
--                último REINDEX correcto del índice (trabajo hnsw_reindex_<indice>).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION vec.medir_hnsw(p_evento text DEFAULT 'antes',
                                          p_duracion_s numeric DEFAULT NULL,
                                          p_indice text DEFAULT NULL)
RETURNS SETOF vec.hnsw_metricas
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    c record;
    tam bigint;
    vivas bigint;
    muertas bigint;
    parts integer;
    bpf numeric;
    base numeric;
    dur numeric;
    fila vec.hnsw_metricas;
BEGIN
    IF p_evento NOT IN ('construccion', 'antes', 'despues') THEN
        RAISE EXCEPTION 'vec.medir_hnsw: evento desconocido (%)', p_evento;
    END IF;
    FOR c IN SELECT * FROM vec.hnsw_catalogo WHERE activo AND (p_indice IS NULL OR indice = p_indice) ORDER BY indice LOOP
        -- Índice (o, si está particionado, la suma de los índices de sus particiones).
        SELECT coalesce(sum(pg_relation_size(t.relid)), 0), count(*)
          INTO tam, parts
          FROM pg_partition_tree(to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice)) t
         WHERE t.isleaf;
        IF parts = 0 AND to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice) IS NOT NULL THEN
            -- índice de una tabla sin particionar: pg_partition_tree no lo devuelve
            tam := pg_relation_size(to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice));
            parts := 1;
        END IF;
        IF to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice) IS NULL THEN
            tam := NULL; parts := NULL;
        END IF;
        -- Filas vivas: las estadísticas de actividad y, si aún no se han volcado (tabla recién cargada),
        -- las de ANALYZE (reltuples).
        SELECT coalesce(nullif(sum(s.n_live_tup), 0), sum(greatest(k.reltuples, 0))::bigint, 0),
               coalesce(sum(s.n_dead_tup), 0)
          INTO vivas, muertas
          FROM vec.hnsw_hojas(c.tabla::regclass) h
          JOIN pg_class k ON k.oid = h
          LEFT JOIN pg_stat_all_tables s ON s.relid = h;
        bpf := CASE WHEN tam IS NOT NULL AND vivas > 0 THEN round(tam::numeric / vivas, 1) END;
        SELECT min(m.bytes_por_fila) INTO base FROM vec.hnsw_metricas m
         WHERE m.indice = c.indice AND m.evento IN ('construccion', 'despues') AND m.bytes_por_fila IS NOT NULL;
        -- Una construcción o un REINDEX fijan la línea base: su propia medición no tiene hinchazón.
        IF base IS NULL AND p_evento IN ('construccion', 'despues') THEN
            base := bpf;
        END IF;
        dur := p_duracion_s;
        IF dur IS NULL AND p_evento = 'despues' THEN
            BEGIN
                EXECUTE 'SELECT round(extract(epoch FROM (r.end_time - r.start_time))::numeric, 1)
                           FROM cron.job_run_details r JOIN cron.job j ON j.jobid = r.jobid
                          WHERE j.jobname = $1 AND r.status = ''succeeded'' ORDER BY r.start_time DESC LIMIT 1'
                    INTO dur USING 'hnsw_reindex_' || c.indice;
            EXCEPTION WHEN OTHERS THEN
                dur := NULL;   -- sin pg_cron o sin permiso: la duración queda sin registrar
            END;
        END IF;
        INSERT INTO vec.hnsw_metricas (indice, evento, tamano_bytes, filas_vivas, filas_muertas, bytes_por_fila,
                                       hinchazon_pct, duracion_s, maintenance_work_mem, max_parallel_workers, particiones)
        VALUES (c.indice, p_evento, tam, vivas, muertas, bpf,
                CASE WHEN bpf IS NOT NULL AND base IS NOT NULL THEN round(100 * (bpf - base) / base, 1) END,
                dur, current_setting('maintenance_work_mem'),
                current_setting('max_parallel_maintenance_workers')::int, parts)
        RETURNING * INTO fila;
        RETURN NEXT fila;
    END LOOP;
END $$;
COMMENT ON FUNCTION vec.medir_hnsw(text, numeric, text) IS
'KC-DB-08-2: registra tamaño, filas vivas/muertas, bytes por fila e hinchazón de cada HNSW del catálogo (evento construccion|antes|despues).';
REVOKE ALL ON FUNCTION vec.medir_hnsw(text, numeric, text) FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- Estado: ¿el índice existe y es válido? ¿cuántas particiones no tienen índice válido adjunto?
-- Una construcción concurrente interrumpida (cancelada, caída del servidor) deja un índice INVÁLIDO:
-- no lo usa ninguna consulta pero se actualiza en cada escritura. Se detecta aquí y se reconstruye
-- ejecutando de nuevo db/06_indices_concurrentes.sql.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vec.v_hnsw_estado AS
SELECT c.indice,
       c.tabla,
       idx.oid IS NOT NULL                                           AS existe,
       coalesce(ix.indisvalid, false)                                AS valido,
       coalesce(ix.indisready, false)                                AS listo,
       (SELECT count(*) FROM vec.hnsw_hojas(c.tabla::regclass) h
         WHERE NOT EXISTS (SELECT 1 FROM pg_index pi
                             JOIN pg_class pc ON pc.oid = pi.indexrelid
                             JOIN pg_am am ON am.oid = pc.relam AND am.amname = 'hnsw'
                            WHERE pi.indrelid = h AND pi.indisvalid))::int AS particiones_sin_indice,
       (SELECT count(*) FROM pg_index pi2
          JOIN pg_class pc2 ON pc2.oid = pi2.indexrelid
         WHERE (pi2.indrelid = c.tabla::regclass OR pi2.indrelid IN (SELECT h FROM vec.hnsw_hojas(c.tabla::regclass) h))
           AND NOT pi2.indisvalid)::int                              AS indices_invalidos,
       pg_size_pretty(CASE WHEN idx.relkind = 'i' THEN pg_relation_size(idx.oid)
                           ELSE coalesce((SELECT sum(pg_relation_size(t.relid))
                                            FROM pg_partition_tree(idx.oid) t WHERE t.isleaf), 0) END) AS tamano
  FROM vec.hnsw_catalogo c
  LEFT JOIN pg_class idx ON idx.oid = to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice)
  LEFT JOIN pg_index ix ON ix.indexrelid = idx.oid
 WHERE c.activo;
COMMENT ON VIEW vec.v_hnsw_estado IS
'KC-DB-08-2: estado de cada HNSW del catálogo. valido = false o particiones_sin_indice > 0 o indices_invalidos > 0 => ejecutar db/06_indices_concurrentes.sql.';
REVOKE ALL ON vec.v_hnsw_estado FROM PUBLIC, kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- REINDEX CONCURRENTLY programado con pg_cron (domingo, UTC). REINDEX CONCURRENTLY no puede ir dentro
-- de una función ni de un bloque de varias sentencias, así que cada paso es un trabajo propio de una
-- sola sentencia, escalonados para que no coincidan (cada uno usa maintenance_work_mem, 2 GB en el rol
-- kallicode_admin). El escalonado hay que ajustarlo al volumen: docs/runbook-vectorial.md, sección 4.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regnamespace('cron') IS NULL THEN
        RAISE NOTICE '044: pg_cron no está instalada: no se programa el REINDEX semanal de los HNSW (docs/runbook-vectorial.md, sección 4).';
        RETURN;
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'cron', 'USAGE') THEN
        RAISE NOTICE '044: kallicode_admin no tiene USAGE sobre el esquema cron: vuelva a ejecutar db/00_extensiones.sql como superusuario y programe los trabajos con el bloque de la sección 4 de docs/runbook-vectorial.md.';
        RETURN;
    END IF;
    PERFORM cron.schedule('hnsw_medir_antes',   '0 2 * * 0',  $c$SELECT vec.medir_hnsw('antes')$c$);
    PERFORM cron.schedule('hnsw_reindex_ticket_embeddings_hnsw_ix',     '15 2 * * 0',
                          'REINDEX INDEX CONCURRENTLY vec.ticket_embeddings_hnsw_ix');
    PERFORM cron.schedule('hnsw_reindex_doc_chunks_hnsw_ix',            '15 3 * * 0',
                          'REINDEX INDEX CONCURRENTLY vec.doc_chunks_hnsw_ix');
    PERFORM cron.schedule('hnsw_reindex_definition_embeddings_hnsw_ix', '15 4 * * 0',
                          'REINDEX INDEX CONCURRENTLY vec.definition_embeddings_hnsw_ix');
    PERFORM cron.schedule('hnsw_medir_despues', '0 6 * * 0',  $c$SELECT vec.medir_hnsw('despues')$c$);
END $$;
