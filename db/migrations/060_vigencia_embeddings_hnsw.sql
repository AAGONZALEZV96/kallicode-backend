-- ============================================================================
-- KALLICODE · 060_vigencia_embeddings_hnsw.sql
-- KC-CM-11-4 — Mantenimiento de los índices HNSW y vigencia de los embeddings BGE-M3.
--
-- Dos problemas del refresco diario de CodeMapping:
--
--   1. Nada garantizaba que el embedding de un nodo correspondiera a su descripción VIGENTE.
--      vec.definition_embeddings.texto_hash es el hash de su propia columna descripcion (el texto que
--      se embebió), así que compararlos dentro de la fila no prueba nada. La fuente de verdad son los
--      nodos de cm: el texto canónico de un símbolo (nombre, firma y descripción LLM) y el de una
--      definición (nombre y descripción). La descripción LLM de los símbolos no se guardaba en ningún
--      sitio (cm.symbols.descripcion existía, pero nadie la escribía): desde KC-CM-11-4 la escribe el
--      cargador y esta migración la recupera del texto ya embebido para los nodos existentes.
--
--   2. Cada UPSERT del refresco deja la versión vieja del vector como tupla muerta; en un HNSW el nodo
--      muerto sigue en el grafo hasta que un VACUUM lo desconecta y repara a sus vecinos, y el grafo no
--      se compacta hasta un REINDEX (044, semanal). Sin VACUUM entre dos REINDEX el índice crece y la
--      búsqueda gasta candidatos en nodos que ya no existen. Medido en packages/core/tests/
--      bench_hnsw_rotacion.py (docs/vigencia_embeddings_hnsw.md).
--
-- Objetos:
--   cm.recortar_espacios / cm.texto_embedding_simbolo / cm.texto_embedding_definicion / vec.hash_texto
--        la MISMA fórmula que kallicode_codemapping.embeddings (texto_embedding_*, hash_texto_embedding);
--        test_vigencia_embeddings compara las dos implementaciones.
--   vec.vigencia_detalle()      por tenant y capa: nodos, con embedding, vigentes, obsoletos, sin embedding
--   vec.vigencia_embeddings()   la del tenant de la sesión (RLS), con su ratio
--   vec.embeddings_a_reparar()  los nodos del tenant que hay que (re)embeber y por qué
--   vec.vigencia_global()       agregado de todos los tenants para la métrica (SECURITY DEFINER, solo cuentas)
--   vec.estado_hnsw()           tamaño, filas vivas y muertas e hinchazón EN VIVO de cada HNSW, para la
--                               métrica (SECURITY DEFINER: vec.hnsw_* es solo de kallicode_admin)
--   pg_cron                     VACUUM (ANALYZE) diario de las colecciones que rota el refresco; el REINDEX
--                               CONCURRENTLY semanal de 044 se mantiene
--
-- Sin tablas, columnas ni índices nuevos. El backfill solo rellena cm.symbols.descripcion donde es NULL y
-- el texto embebido permite reconstruirla exactamente.
-- Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ---------------------------------------------------------------------------
-- 1. Texto canónico y hash. str.strip() de Python recorta los 29 caracteres para los que
--    str.isspace() es verdadero; btrim() solo recorta espacios salvo que se le pasen todos.
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.recortar_espacios(p_texto text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT btrim(p_texto, U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')
$$;

CREATE FUNCTION cm.texto_embedding_simbolo(p_nombre text, p_firma text, p_descripcion text)
    RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT cm.recortar_espacios(p_nombre || ' ' || p_firma || ' ' || coalesce(p_descripcion, ''))
$$;

CREATE FUNCTION cm.texto_embedding_definicion(p_nombre text, p_descripcion text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT cm.recortar_espacios(p_nombre || ' ' || coalesce(p_descripcion, ''))
$$;

-- Convención de 001_vec_modelo_embedding_hash.sql: sha256(texto)[:32].
CREATE FUNCTION vec.hash_texto(p_texto text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT left(encode(sha256(convert_to(p_texto, 'UTF8')), 'hex'), 32)
$$;

COMMENT ON FUNCTION cm.texto_embedding_simbolo(text, text, text) IS
'KC-CM-11-4: texto que se embebe por un símbolo. Idéntico a kallicode_codemapping.embeddings.texto_embedding_simbolo.';
COMMENT ON FUNCTION cm.texto_embedding_definicion(text, text) IS
'KC-CM-11-4: texto que se embebe por una definición. Idéntico a kallicode_codemapping.embeddings.texto_embedding_definicion.';
COMMENT ON FUNCTION vec.hash_texto(text) IS
'KC-CM-11-4: sha256(texto)[:32], la convención de vec.*.texto_hash. Idéntico a kallicode_codemapping.embeddings.hash_texto_embedding.';

-- ---------------------------------------------------------------------------
-- 2. Backfill de cm.symbols.descripcion: el texto embebido es «nombre firma descripción» recortado;
--    se recupera la descripción y solo se escribe si al recomponer el texto canónico se obtiene
--    EXACTAMENTE el texto embebido y su hash (si no, queda NULL y el nodo saldrá como obsoleto).
-- ---------------------------------------------------------------------------
UPDATE cm.symbols s
   SET descripcion = x.descripcion
  FROM (
      SELECT s2.tenant_id, s2.id,
             substr(d.descripcion, char_length(s2.name || ' ' || s2.signature || ' ') + 1) AS descripcion,
             d.descripcion AS embebido, d.texto_hash
        FROM cm.symbols s2
        JOIN vec.definition_embeddings d
          ON d.tenant_id = s2.tenant_id AND d.nodo_ref = s2.id AND d.capa = 'symbol'
       WHERE s2.descripcion IS NULL
         AND left(d.descripcion, char_length(s2.name || ' ' || s2.signature || ' '))
             = s2.name || ' ' || s2.signature || ' '
  ) x
 WHERE s.tenant_id = x.tenant_id AND s.id = x.id
   AND x.descripcion <> ''
   AND cm.texto_embedding_simbolo(s.name, s.signature, x.descripcion) = x.embebido
   AND vec.hash_texto(x.embebido) = x.texto_hash;

-- ---------------------------------------------------------------------------
-- 3. Vigencia. Un nodo es VIGENTE si tiene embedding del modelo vigente cuyo texto_hash es el hash de
--    su texto canónico calculado AHORA desde cm.*; OBSOLETO si tiene embedding pero no cumple eso
--    (descripción cambiada o modelo distinto); SIN EMBEDDING si no tiene fila en
--    vec.definition_embeddings. Capas: las que escribe el pipeline (symbol, definition).
--    SECURITY INVOKER: con kallicode_app, RLS limita al tenant de la sesión.
-- ---------------------------------------------------------------------------
CREATE FUNCTION vec.vigencia_detalle(p_tenant text DEFAULT NULL, p_modelo text DEFAULT 'bge-m3')
    RETURNS TABLE (tenant_id text, capa text, nodos bigint, con_embedding bigint, vigentes bigint,
                   obsoletos bigint, sin_embedding bigint)
    LANGUAGE sql STABLE AS $$
    WITH simbolos AS (
        SELECT s.tenant_id,
               d.nodo_ref IS NOT NULL AS tiene,
               d.modelo_embedding = p_modelo
                 AND d.texto_hash = vec.hash_texto(
                       cm.texto_embedding_simbolo(s.name, s.signature, s.descripcion)) AS vigente
          FROM cm.symbols s
          LEFT JOIN vec.definition_embeddings d
            ON d.tenant_id = s.tenant_id AND d.nodo_ref = s.id
         WHERE p_tenant IS NULL OR s.tenant_id = p_tenant
    ),
    definiciones AS (
        SELECT f.tenant_id,
               d.nodo_ref IS NOT NULL AS tiene,
               d.modelo_embedding = p_modelo
                 AND d.texto_hash = vec.hash_texto(
                       cm.texto_embedding_definicion(f.nombre, f.descripcion)) AS vigente
          FROM cm.definitions f
          LEFT JOIN vec.definition_embeddings d
            ON d.tenant_id = f.tenant_id AND d.nodo_ref = f.id
         WHERE p_tenant IS NULL OR f.tenant_id = p_tenant
    )
    SELECT x.tenant_id, 'symbol', count(*), count(*) FILTER (WHERE x.tiene),
           count(*) FILTER (WHERE x.tiene AND x.vigente),
           count(*) FILTER (WHERE x.tiene AND NOT x.vigente),
           count(*) FILTER (WHERE NOT x.tiene)
      FROM simbolos x GROUP BY x.tenant_id
    UNION ALL
    SELECT y.tenant_id, 'definition', count(*), count(*) FILTER (WHERE y.tiene),
           count(*) FILTER (WHERE y.tiene AND y.vigente),
           count(*) FILTER (WHERE y.tiene AND NOT y.vigente),
           count(*) FILTER (WHERE NOT y.tiene)
      FROM definiciones y GROUP BY y.tenant_id
$$;

CREATE FUNCTION vec.vigencia_embeddings(p_modelo text DEFAULT 'bge-m3')
    RETURNS TABLE (capa text, nodos bigint, con_embedding bigint, vigentes bigint, obsoletos bigint,
                   sin_embedding bigint, ratio numeric)
    LANGUAGE sql STABLE AS $$
    SELECT v.capa, v.nodos, v.con_embedding, v.vigentes, v.obsoletos, v.sin_embedding,
           CASE WHEN v.nodos = 0 THEN 1 ELSE round(v.vigentes::numeric / v.nodos, 4) END
      FROM vec.vigencia_detalle(core.current_tenant(), p_modelo) v
     ORDER BY v.capa
$$;

COMMENT ON FUNCTION vec.vigencia_embeddings(text) IS
'KC-CM-11-4: vigencia de los embeddings del tenant de la sesión por capa (symbol, definition): vigente = texto_hash igual al hash del texto canónico recalculado desde cm.* y modelo igual a p_modelo. ratio = vigentes / nodos.';

-- Lo que hay que (re)embeber, con los campos de cm.* que forman el texto canónico.
CREATE FUNCTION vec.embeddings_a_reparar(p_limite integer DEFAULT 1000,
                                         p_modelo text DEFAULT 'bge-m3')
    RETURNS TABLE (capa text, nodo_ref text, nombre text, firma text, descripcion text, ruta text,
                   kind text, modulo text, motivo text)
    LANGUAGE sql STABLE AS $$
    SELECT * FROM (
        SELECT 'symbol'::text, s.id, s.name, s.signature, s.descripcion, f.path, s.kind,
               CAST(NULL AS text),
               CASE WHEN d.nodo_ref IS NULL THEN 'sin_embedding'
                    WHEN d.modelo_embedding <> p_modelo THEN 'modelo'
                    ELSE 'obsoleto' END
          FROM cm.symbols s
          JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
          LEFT JOIN vec.definition_embeddings d
            ON d.tenant_id = s.tenant_id AND d.nodo_ref = s.id
         WHERE s.tenant_id = core.current_tenant()
           AND (d.nodo_ref IS NULL OR d.modelo_embedding <> p_modelo
                OR d.texto_hash <> vec.hash_texto(
                     cm.texto_embedding_simbolo(s.name, s.signature, s.descripcion)))
        UNION ALL
        SELECT 'definition', f.id, f.nombre, CAST(NULL AS text), f.descripcion, CAST(NULL AS text),
               f.kind, f.modulo,
               CASE WHEN d.nodo_ref IS NULL THEN 'sin_embedding'
                    WHEN d.modelo_embedding <> p_modelo THEN 'modelo'
                    ELSE 'obsoleto' END
          FROM cm.definitions f
          LEFT JOIN vec.definition_embeddings d
            ON d.tenant_id = f.tenant_id AND d.nodo_ref = f.id
         WHERE f.tenant_id = core.current_tenant()
           AND (d.nodo_ref IS NULL OR d.modelo_embedding <> p_modelo
                OR d.texto_hash <> vec.hash_texto(
                     cm.texto_embedding_definicion(f.nombre, f.descripcion)))
    ) r (capa, nodo_ref, nombre, firma, descripcion, ruta, kind, modulo, motivo)
     ORDER BY r.capa, r.nodo_ref COLLATE "C"
     LIMIT p_limite
$$;

COMMENT ON FUNCTION vec.embeddings_a_reparar(integer, text) IS
'KC-CM-11-4: nodos del tenant de la sesión sin embedding o con embedding obsoleto (texto o modelo), con los campos de cm.* que forman su texto canónico. La usa el paso de reparación del refresco diario.';

-- Agregado de todos los tenants (sin datos de ninguno: solo cuentas) para la métrica del worker.
CREATE FUNCTION vec.vigencia_global(p_modelo text DEFAULT 'bge-m3', p_umbral numeric DEFAULT 0.99)
    RETURNS TABLE (capa text, tenants bigint, nodos bigint, vigentes bigint, obsoletos bigint,
                   sin_embedding bigint, ratio numeric, tenants_bajo_umbral bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT v.capa, count(*), sum(v.nodos)::bigint, sum(v.vigentes)::bigint, sum(v.obsoletos)::bigint,
           sum(v.sin_embedding)::bigint,
           CASE WHEN sum(v.nodos) = 0 THEN 1 ELSE round(sum(v.vigentes) / sum(v.nodos), 4) END,
           count(*) FILTER (WHERE v.nodos > 0 AND v.vigentes::numeric / v.nodos < p_umbral)
      FROM vec.vigencia_detalle(NULL, p_modelo) v
     GROUP BY v.capa
     ORDER BY v.capa
$$;

COMMENT ON FUNCTION vec.vigencia_global(text, numeric) IS
'KC-CM-11-4: vigencia de los embeddings de todos los tenants por capa (solo cuentas), y cuántos tenants quedan por debajo de p_umbral. SECURITY DEFINER para la métrica del worker.';

-- ---------------------------------------------------------------------------
-- 4. Estado en vivo de los HNSW para la métrica (misma medida que vec.medir_hnsw, sin registrar nada).
--    hinchazon_pct: bytes de índice por fila viva frente al mejor valor registrado en vec.hnsw_metricas
--    tras una construcción o un REINDEX (NULL si nunca se midió una línea base).
--    muertas_pct: tuplas muertas del heap, lo que limpia el VACUUM diario.
-- ---------------------------------------------------------------------------
CREATE FUNCTION vec.estado_hnsw()
    RETURNS TABLE (indice text, tabla text, tamano_bytes bigint, filas_vivas bigint,
                   filas_muertas bigint, bytes_por_fila numeric, linea_base_bytes_por_fila numeric,
                   hinchazon_pct numeric, muertas_pct numeric, ultimo_vacuum timestamptz,
                   ultima_reconstruccion timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    WITH idx AS (
        SELECT c.indice, c.tabla,
               to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice) AS oid_indice
          FROM vec.hnsw_catalogo c
         WHERE c.activo
    ),
    medida AS (
        SELECT i.indice, i.tabla,
               CASE WHEN i.oid_indice IS NULL THEN NULL
                    ELSE coalesce((SELECT sum(pg_relation_size(t.relid))
                                     FROM pg_partition_tree(i.oid_indice) t
                                    WHERE t.isleaf AND t.relid <> i.oid_indice),
                                  pg_relation_size(i.oid_indice))::bigint END AS tamano,
               (SELECT coalesce(nullif(sum(s.n_live_tup), 0), sum(greatest(k.reltuples, 0))::bigint, 0)
                  FROM vec.hnsw_hojas(i.tabla::regclass) h
                  JOIN pg_class k ON k.oid = h
                  LEFT JOIN pg_stat_all_tables s ON s.relid = h)::bigint AS vivas,
               (SELECT coalesce(sum(s.n_dead_tup), 0)
                  FROM vec.hnsw_hojas(i.tabla::regclass) h
                  LEFT JOIN pg_stat_all_tables s ON s.relid = h)::bigint AS muertas,
               (SELECT max(greatest(s.last_vacuum, s.last_autovacuum))
                  FROM vec.hnsw_hojas(i.tabla::regclass) h
                  LEFT JOIN pg_stat_all_tables s ON s.relid = h) AS ultimo_vacuum,
               (SELECT min(m.bytes_por_fila) FROM vec.hnsw_metricas m
                 WHERE m.indice = i.indice AND m.evento IN ('construccion', 'despues')
                   AND m.bytes_por_fila IS NOT NULL) AS base,
               (SELECT max(m.creado_en) FROM vec.hnsw_metricas m
                 WHERE m.indice = i.indice AND m.evento IN ('construccion', 'despues')) AS reconstruido
          FROM idx i
    )
    SELECT m.indice, m.tabla, m.tamano, m.vivas, m.muertas,
           CASE WHEN m.vivas > 0 THEN round(m.tamano::numeric / m.vivas, 1) END,
           m.base,
           CASE WHEN m.vivas > 0 AND m.base > 0
                THEN round(100 * (m.tamano::numeric / m.vivas - m.base) / m.base, 1) END,
           CASE WHEN m.vivas + m.muertas > 0
                THEN round(100 * m.muertas::numeric / (m.vivas + m.muertas), 1) ELSE 0 END,
           m.ultimo_vacuum, m.reconstruido
      FROM medida m
     ORDER BY m.indice
$$;

COMMENT ON FUNCTION vec.estado_hnsw() IS
'KC-CM-11-4: tamaño, filas vivas/muertas, bytes por fila e hinchazón en vivo de cada HNSW de vec.hnsw_catalogo (sin registrar nada). SECURITY DEFINER para la métrica del worker de CodeMapping.';

REVOKE ALL ON FUNCTION vec.vigencia_detalle(text, text)        FROM PUBLIC;
REVOKE ALL ON FUNCTION vec.vigencia_embeddings(text)           FROM PUBLIC;
REVOKE ALL ON FUNCTION vec.embeddings_a_reparar(integer, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION vec.vigencia_global(text, numeric)      FROM PUBLIC;
REVOKE ALL ON FUNCTION vec.estado_hnsw()                       FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm.recortar_espacios(text)                      TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.texto_embedding_simbolo(text, text, text)    TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.texto_embedding_definicion(text, text)       TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION vec.hash_texto(text)                            TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION vec.vigencia_detalle(text, text)                TO kallicode_app;
GRANT EXECUTE ON FUNCTION vec.vigencia_embeddings(text)                   TO kallicode_app;
GRANT EXECUTE ON FUNCTION vec.embeddings_a_reparar(integer, text)         TO kallicode_app;
GRANT EXECUTE ON FUNCTION vec.vigencia_global(text, numeric)              TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION vec.estado_hnsw()                               TO kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 5. Parámetros de los HNSW que rota el refresco: m = 24, ef_construction = 128 (antes 16 / 64).
--    Con VACUUM diario, pgvector desconecta los nodos muertos y repara a sus vecinos con una búsqueda
--    de ef_construction candidatos: con 16 / 64 esas reparaciones degradan el grafo y el recall@10 cae
--    ~3 puntos en una semana de rotación; con 24 / 128 se mantiene (bench_hnsw_rotacion.py). El tamaño
--    no cambia: con 1024 dimensiones en float4 cada elemento ocupa una página de 8 KB.
--
--    Cómo se aplica sin bloquear: PostgreSQL no admite ALTER INDEX ... SET sobre el índice de una tabla
--    particionada, pero sí sobre el de cada partición, y REINDEX conserva las opciones del índice. Así que
--    vec.alinear_parametros_hnsw() pone en cada índice hoja los parámetros de vec.hnsw_catalogo (cambio de
--    metadatos, instantáneo) y el REINDEX INDEX CONCURRENTLY semanal de 044 reconstruye el grafo con ellos.
--    Una partición creada después hereda las opciones del padre (16 / 64): el trabajo diario
--    hnsw_alinear_parametros la corrige antes de su VACUUM. vec.hnsw_catalogo pasa a ser la fuente de
--    los parámetros (db/02_indexes.sql es el esquema basal; db/06_indices_concurrentes.sql ya lee el
--    catálogo).
-- ---------------------------------------------------------------------------
-- Medido con embeddings BGE-M3 reales ampliados a 20.000 filas, 7 días al 5 % diario, ef_search = 100:
--   definiciones: mínimo 0,964 (16/64) frente a 0,989 (24/128); docs: 0,944 frente a 0,976.
UPDATE vec.hnsw_catalogo SET m = 24, ef_construction = 128
 WHERE indice IN ('definition_embeddings_hnsw_ix', 'doc_chunks_hnsw_ix');

COMMENT ON TABLE vec.hnsw_catalogo IS
'KC-DB-08-2 / KC-CM-11-4: índices HNSW de vec.*: fuente de sus parámetros. Los leen db/06_indices_concurrentes.sql (construcción), vec.alinear_parametros_hnsw() (opciones de cada partición, aplicadas por el REINDEX semanal) y vec.medir_hnsw().';

CREATE FUNCTION vec.alinear_parametros_hnsw()
    RETURNS TABLE (indice text, indice_hoja text, antes text[], despues text[], aplicado boolean)
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    SET lock_timeout = '5s' AS $$
DECLARE
    c record;
    h record;
    objetivo text[];
BEGIN
    FOR c IN SELECT k.indice, k.tabla, k.m, k.ef_construction FROM vec.hnsw_catalogo k
              WHERE k.activo ORDER BY k.indice LOOP
        objetivo := ARRAY['m=' || c.m, 'ef_construction=' || c.ef_construction];
        FOR h IN
            SELECT pc.oid::regclass AS hoja, coalesce(pc.reloptions, '{}') AS opciones
              FROM pg_class pc
             WHERE pc.oid IN (
                   SELECT t.relid FROM pg_partition_tree(
                          to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice)) t
                    WHERE t.isleaf
                   UNION
                   SELECT to_regclass(split_part(c.tabla, '.', 1) || '.' || c.indice))
               AND pc.relkind = 'i'
             ORDER BY 1
        LOOP
            CONTINUE WHEN h.opciones @> objetivo;
            indice := c.indice; indice_hoja := h.hoja::text; antes := h.opciones; despues := objetivo;
            BEGIN
                EXECUTE format('ALTER INDEX %s SET (m = %s, ef_construction = %s)',
                               h.hoja, c.m, c.ef_construction);
                aplicado := true;
            EXCEPTION WHEN lock_not_available THEN
                aplicado := false;   -- índice ocupado: lo reintenta la ejecución siguiente
            END;
            RETURN NEXT;
        END LOOP;
    END LOOP;
END $$;

COMMENT ON FUNCTION vec.alinear_parametros_hnsw() IS
'KC-CM-11-4: pone en cada índice hoja (partición) de los HNSW del catálogo los m y ef_construction de vec.hnsw_catalogo. Solo cambia opciones: el grafo se reconstruye con ellas en el siguiente REINDEX (semanal, 044). Devuelve los índices tocados.';
REVOKE ALL ON FUNCTION vec.alinear_parametros_hnsw() FROM PUBLIC;

SELECT count(*) FROM vec.alinear_parametros_hnsw();

-- ---------------------------------------------------------------------------
-- 6. Rutina diaria con pg_cron (ventana 01:00-01:30 UTC, antes de la del domingo de 044, que empieza a
--    las 02:00 con hnsw_medir_antes). VACUUM no admite transacción ni función: cada trabajo es una sola
--    sentencia. Solo las colecciones que rota el refresco diario de CodeMapping; vec.ticket_embeddings la
--    escribe el backend por ticket y le basta el autovacuum. Ventana y coste: docs/vigencia_embeddings_hnsw.md
--    y docs/runbook-vectorial.md (sección 4).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regnamespace('cron') IS NULL THEN
        RAISE NOTICE '060: pg_cron no está instalada: no se programa el VACUUM diario de los HNSW (docs/runbook-vectorial.md, sección 4).';
        RETURN;
    END IF;
    IF NOT has_schema_privilege('kallicode_admin', 'cron', 'USAGE') THEN
        RAISE NOTICE '060: kallicode_admin no tiene USAGE sobre el esquema cron: programe los trabajos con el bloque de la sección 4 de docs/runbook-vectorial.md.';
        RETURN;
    END IF;
    PERFORM cron.schedule('hnsw_alinear_parametros', '50 0 * * *',
                          'SELECT * FROM vec.alinear_parametros_hnsw()');
    PERFORM cron.schedule('hnsw_vacuum_definition_embeddings', '0 1 * * *',
                          'VACUUM (ANALYZE) vec.definition_embeddings');
    PERFORM cron.schedule('hnsw_vacuum_doc_chunks', '30 1 * * *',
                          'VACUUM (ANALYZE) vec.doc_chunks');
END $$;
