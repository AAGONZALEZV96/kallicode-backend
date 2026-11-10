-- ============================================================================
-- KALLICODE · 043_busqueda_hibrida.sql
-- KC-CM-08-4 — Búsqueda semántica híbrida sobre las capas de documentación y
-- definiciones: similitud de embeddings (HNSW) + filtros relacionales por
-- tenant, capa y módulo, con iterative_scan para que el filtro no hunda el
-- recall.
--
--   cm.buscar_hibrido(embedding, capas, modulo, k, incluir_sin_curar)
--       Top-k por similitud coseno entre:
--         * vec.definition_embeddings  capas file | symbol | db_table |
--                                      db_column | definition | concept
--         * vec.doc_chunks             capa doc
--         * cm.nuggets                 capa nugget (por el embedding del chunk
--                                      que los respalda, evidencia_ref)
--       - tenant: core.current_tenant() como predicado EXPLÍCITO de cada
--         recorrido vectorial (además de RLS), para que aparezca en el plan y
--         la poda de particiones LIST por tenant (037) actúe al planificar.
--       - capa: lista de capas a buscar (NULL = todas).
--       - módulo: prefijo de ruta con semántica de carpeta ('src/billing'
--         casa 'src/billing' y 'src/billing/x', nunca 'src/billingx'), como
--         module_conventions en cm-consulta-v1. Sobre definition_embeddings
--         usa la columna modulo; un chunk está en el módulo si está enlazado
--         (cm.doc_chunk_links) a un fichero, a un símbolo de un fichero o a
--         una definición del módulo.
--       - curación: por defecto NUNCA devuelve conceptos ni nuggets sin curar.
--         Nugget curado = 'aprobado' (041); 'propuesto' solo con
--         incluir_sin_curar; 'rechazado' nunca. Concepto curado =
--         cm.concepto_curado(): resuelto por una persona (curado_por) o con
--         confianza >= cm.umbral_confianza() y sin revisión pendiente en
--         cm.curation_queue. Un concepto sin confianza ni curador está sin curar.
--         La consulta aplica esa misma regla como conjunto (CTE
--         conceptos_curados), no fila a fila; un test compara ambas.
--       - iterative_scan: aplica vec.configurar_busqueda('diseno') (038:
--         relaxed_order, ef_search 400, max_scan_tuples 5000, calibrado para
--         recall@10 >= 0,90 con un tenant del 1 % del corpus). Los SET de la
--         función acotan esos parámetros a la llamada: al volver, la
--         transacción del llamante conserva los suyos.
--       Cada recorrido vectorial es `ORDER BY embedding <=> q LIMIT k` (lo que
--       usa el índice HNSW); con relaxed_order el orden puede llegar
--       ligeramente alterado, así que el resultado se reordena al final por
--       distancia y se desempata por capa e id (collation "C"): determinista.
--
--   cm.explicar_busqueda_hibrida(...) -> jsonb
--       EXPLAIN (FORMAT JSON [, ANALYZE]) de EXACTAMENTE la misma consulta, con
--       los mismos parámetros. EXPLAIN sobre cm.buscar_hibrido solo mostraría
--       un Function Scan; esta es la forma de inspeccionar el plan real (el
--       test de aislamiento comprueba que el filtro de tenant está en él).
--
--   La consulta vive una sola vez en cm.busqueda_hibrida_sql(); las dos
--   funciones la ejecutan con EXECUTE ... USING (plan a medida por llamada:
--   poda de particiones por tenant y descarte de capas no pedidas).
--
-- No es destructiva: solo CREATE FUNCTION. No crea índices: usa los HNSW de
-- 037 y definition_emb_tenant_ix (tenant_id, capa, modulo).
-- Sin BEGIN/COMMIT: el runner abre la transacción (db/migrations/README.md).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- Los SET hnsw.* de las funciones se validan al crearlas. Si la biblioteca de
-- pgvector aún no está cargada en esta sesión, hnsw.* serían «placeholders» y
-- un rol sin superusuario no puede fijarlos: se fuerza la carga.
SELECT '[0]'::vector(1);

-- ---------------------------------------------------------------------------
-- 1. Curación de un concepto
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.concepto_curado(p_tenant text, p_concepto text) RETURNS boolean
    LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1
          FROM cm.concepts c
         WHERE c.tenant_id = p_tenant
           AND c.id = p_concepto
           AND (c.curado_por IS NOT NULL
                OR (c.confianza >= cm.umbral_confianza()
                    AND NOT EXISTS (
                        SELECT 1
                          FROM cm.curation_queue q
                         WHERE q.tenant_id = c.tenant_id
                           AND q.tipo = 'concepto'
                           AND q.estado = 'pendiente'
                           AND q.payload @> jsonb_build_object('id', c.id))))
    )
$$;

COMMENT ON FUNCTION cm.concepto_curado(text, text) IS
'KC-CM-08-4: true si el concepto está curado: resuelto por una persona (curado_por) o con confianza >= cm.umbral_confianza() y sin revisión pendiente en cm.curation_queue. Sin confianza ni curador = sin curar.';

-- ---------------------------------------------------------------------------
-- 2. Capas y validación de parámetros
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.capas_busqueda() RETURNS text[]
    LANGUAGE sql IMMUTABLE AS $$
    SELECT ARRAY['file', 'symbol', 'db_table', 'db_column', 'definition', 'concept', 'doc', 'nugget']
$$;

COMMENT ON FUNCTION cm.capas_busqueda() IS
'KC-CM-08-4: capas admitidas por cm.buscar_hibrido: los kinds de vec.definition_embeddings (de_capa_chk), doc (vec.doc_chunks) y nugget (cm.nuggets). kallicode_core.cm_busqueda.CapaBusqueda declara las mismas.';

-- Devuelve las capas normalizadas (NULL = todas) o lanza invalid_parameter_value.
CREATE FUNCTION cm.validar_busqueda_hibrida(
    p_embedding vector,
    p_capas     text[],
    p_modulo    text,
    p_k         integer
) RETURNS text[]
    LANGUAGE plpgsql IMMUTABLE
    SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    v_desconocidas text[];
BEGIN
    IF p_embedding IS NULL OR vector_dims(p_embedding) <> 1024 THEN
        RAISE EXCEPTION 'cm.buscar_hibrido: el embedding debe tener 1024 dimensiones (BGE-M3), tiene %',
            COALESCE(vector_dims(p_embedding)::text, 'NULL')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_k IS NULL OR p_k < 1 OR p_k > 100 THEN
        RAISE EXCEPTION 'cm.buscar_hibrido: k debe estar entre 1 y 100 (es %)', COALESCE(p_k::text, 'NULL')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_modulo IS NOT NULL AND char_length(p_modulo) > 500 THEN
        RAISE EXCEPTION 'cm.buscar_hibrido: el módulo admite hasta 500 caracteres'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_capas IS NULL THEN
        RETURN cm.capas_busqueda();
    END IF;
    IF cardinality(p_capas) = 0 THEN
        RAISE EXCEPTION 'cm.buscar_hibrido: la lista de capas no puede estar vacía (NULL = todas)'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT array_agg(c ORDER BY c) INTO v_desconocidas
      FROM unnest(p_capas) AS c
     WHERE c IS NULL OR NOT c = ANY (cm.capas_busqueda());
    IF v_desconocidas IS NOT NULL THEN
        RAISE EXCEPTION 'cm.buscar_hibrido: capas desconocidas %; admitidas: %',
            v_desconocidas, cm.capas_busqueda()
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN ARRAY(SELECT DISTINCT c FROM unnest(p_capas) AS c ORDER BY c);
END $$;

-- ---------------------------------------------------------------------------
-- 3. La consulta (única definición)
-- ---------------------------------------------------------------------------
-- $1 vector  embedding de la consulta     $4 text     módulo (NULL = todos)
-- $2 text    tenant de la sesión          $5 integer  k
-- $3 text[]  capas (ya validadas)         $6 boolean  incluir_sin_curar
CREATE FUNCTION cm.busqueda_hibrida_sql() RETURNS text
    LANGUAGE sql IMMUTABLE AS $q$
    SELECT $sql$
WITH chunks_modulo AS MATERIALIZED (
    SELECT l.chunk_id
      FROM cm.doc_chunk_links l
      JOIN cm.files f ON f.tenant_id = l.tenant_id AND f.id = l.nodo_ref
     WHERE l.tenant_id = $2 AND f.tenant_id = $2
       AND (f.path = $4 OR starts_with(f.path, $4 || '/'))
    UNION
    SELECT l.chunk_id
      FROM cm.doc_chunk_links l
      JOIN cm.symbols s ON s.tenant_id = l.tenant_id AND s.id = l.nodo_ref
      JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
     WHERE l.tenant_id = $2 AND s.tenant_id = $2 AND f.tenant_id = $2
       AND (f.path = $4 OR starts_with(f.path, $4 || '/'))
    UNION
    SELECT l.chunk_id
      FROM cm.doc_chunk_links l
      JOIN cm.definitions d ON d.tenant_id = l.tenant_id AND d.id = l.nodo_ref
     WHERE l.tenant_id = $2 AND d.tenant_id = $2
       AND (d.modulo = $4 OR starts_with(d.modulo, $4 || '/'))
),
-- Conceptos curados del tenant: la misma regla que cm.concepto_curado(), calculada UNA vez por
-- consulta (anti-join hash con las revisiones pendientes). Llamar a la función fila a fila cuesta
-- un recorrido de la cola por candidato: con miles de conceptos el plan por btree + sort pasaba
-- de 400 ms (benchmark de 200.000 vectores). Solo se materializa si hay que filtrar conceptos.
conceptos_curados AS MATERIALIZED (
    SELECT c.id
      FROM cm.concepts c
     WHERE c.tenant_id = $2
       AND (c.curado_por IS NOT NULL
            OR (c.confianza >= cm.umbral_confianza()
                AND c.id NOT IN (
                      SELECT q.payload ->> 'id'
                        FROM cm.curation_queue q
                       WHERE q.tenant_id = $2 AND q.tipo = 'concepto' AND q.estado = 'pendiente'
                         AND q.payload ->> 'id' IS NOT NULL)))
),
definiciones AS MATERIALIZED (
    SELECT d.capa, d.id, d.nodo_ref AS referencia, d.modulo, d.nombre AS titulo,
           d.descripcion AS contenido, d.embedding <=> $1 AS distancia
      FROM vec.definition_embeddings d
     WHERE d.tenant_id = $2
       AND d.capa = ANY ($3)
       AND ($4 IS NULL OR d.modulo = $4 OR starts_with(d.modulo, $4 || '/'))
       AND ($6 OR d.capa <> 'concept'
            OR d.nodo_ref IN (SELECT cc.id FROM conceptos_curados cc))
     ORDER BY d.embedding <=> $1
     LIMIT $5
),
documentos AS MATERIALIZED (
    SELECT c.id, c.document_id AS referencia, c.seccion AS titulo, c.content AS contenido,
           c.embedding <=> $1 AS distancia
      FROM vec.doc_chunks c
     WHERE c.tenant_id = $2
       AND 'doc' = ANY ($3)
       AND ($4 IS NULL OR c.id IN (SELECT m.chunk_id FROM chunks_modulo m))
     ORDER BY c.embedding <=> $1
     LIMIT $5
),
chunks_nugget AS MATERIALIZED (
    SELECT c.id, c.embedding <=> $1 AS distancia
      FROM vec.doc_chunks c
     WHERE c.tenant_id = $2
       AND 'nugget' = ANY ($3)
       AND ($4 IS NULL OR c.id IN (SELECT m.chunk_id FROM chunks_modulo m))
       AND EXISTS (
             SELECT 1
               FROM cm.nuggets n
              WHERE n.tenant_id = c.tenant_id AND n.evidencia_ref = c.id
                AND (n.estado = 'aprobado' OR ($6 AND n.estado = 'propuesto')))
     ORDER BY c.embedding <=> $1
     LIMIT $5
),
resultados AS (
    SELECT d.capa, d.id, d.referencia, d.modulo, d.titulo, d.contenido, d.distancia,
           CASE WHEN d.capa = 'concept' THEN
                CASE WHEN d.referencia IN (SELECT cc.id FROM conceptos_curados cc)
                     THEN 'curado' ELSE 'sin_curar' END
           END AS estado_curacion
      FROM definiciones d
    UNION ALL
    SELECT 'doc', dc.id, dc.referencia, NULL, dc.titulo, dc.contenido, dc.distancia, NULL
      FROM documentos dc
    UNION ALL
    SELECT 'nugget', n.id, n.evidencia_ref, NULL, n.seccion, n.regla, cn.distancia,
           CASE WHEN n.estado = 'aprobado' THEN 'curado' ELSE 'sin_curar' END
      FROM chunks_nugget cn
      JOIN cm.nuggets n ON n.tenant_id = $2 AND n.evidencia_ref = cn.id
     WHERE n.estado = 'aprobado' OR ($6 AND n.estado = 'propuesto')
)
SELECT r.capa, r.id, r.referencia, r.modulo, r.titulo, r.contenido, r.estado_curacion,
       LEAST(1, GREATEST(0, 1 - r.distancia))::real AS similitud
  FROM resultados r
 ORDER BY r.distancia, r.capa COLLATE "C", r.id COLLATE "C"
 LIMIT $5
$sql$
$q$;

COMMENT ON FUNCTION cm.busqueda_hibrida_sql() IS
'KC-CM-08-4: texto de la consulta híbrida que ejecutan cm.buscar_hibrido y cm.explicar_busqueda_hibrida (única definición). Parámetros: $1 embedding, $2 tenant, $3 capas, $4 módulo, $5 k, $6 incluir_sin_curar.';

-- ---------------------------------------------------------------------------
-- 4. Búsqueda
-- ---------------------------------------------------------------------------
-- SECURITY INVOKER (por defecto): corre con RLS del tenant de la sesión.
-- Los SET hnsw.* no fijan la calibración (la aplica vec.configurar_busqueda
-- en el cuerpo): solo hacen que PostgreSQL restaure esos tres parámetros al
-- salir de la función, de modo que la búsqueda no altera la transacción del
-- llamante. Sus valores son los por defecto de pgvector.
CREATE FUNCTION cm.buscar_hibrido(
    p_embedding         vector,
    p_capas             text[]  DEFAULT NULL,
    p_modulo            text    DEFAULT NULL,
    p_k                 integer DEFAULT 10,
    p_incluir_sin_curar boolean DEFAULT false
) RETURNS TABLE (
    capa            text,
    id              text,
    referencia      text,
    modulo          text,
    titulo          text,
    contenido       text,
    estado_curacion text,
    similitud       real
)
    -- VOLATILE: fija parámetros de configuración (vec.configurar_busqueda).
    LANGUAGE plpgsql VOLATILE
    SET search_path = pg_catalog, public, pg_temp
    SET hnsw.iterative_scan = 'relaxed_order'
    SET hnsw.ef_search = 40
    SET hnsw.max_scan_tuples = 20000 AS $$
DECLARE
    v_capas text[] := cm.validar_busqueda_hibrida(p_embedding, p_capas, p_modulo, p_k);
BEGIN
    PERFORM vec.configurar_busqueda('diseno');
    RETURN QUERY EXECUTE cm.busqueda_hibrida_sql()
        USING p_embedding, core.current_tenant(), v_capas, NULLIF(btrim(p_modulo), ''), p_k,
              COALESCE(p_incluir_sin_curar, false);
END $$;

COMMENT ON FUNCTION cm.buscar_hibrido(vector, text[], text, integer, boolean) IS
'KC-CM-08-4: búsqueda semántica híbrida (HNSW + filtros por tenant, capa y módulo) sobre vec.definition_embeddings, vec.doc_chunks y cm.nuggets, con iterative_scan (parámetros de ''diseno''). Sin incluir_sin_curar no devuelve conceptos ni nuggets sin curar. Contrato: kallicode_core.cm_busqueda.';

-- ---------------------------------------------------------------------------
-- 5. Plan de ejecución de la misma consulta
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.explicar_busqueda_hibrida(
    p_embedding         vector,
    p_capas             text[]  DEFAULT NULL,
    p_modulo            text    DEFAULT NULL,
    p_k                 integer DEFAULT 10,
    p_incluir_sin_curar boolean DEFAULT false,
    p_analizar          boolean DEFAULT false
) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE
    SET search_path = pg_catalog, public, pg_temp
    SET hnsw.iterative_scan = 'relaxed_order'
    SET hnsw.ef_search = 40
    SET hnsw.max_scan_tuples = 20000 AS $$
DECLARE
    v_capas text[] := cm.validar_busqueda_hibrida(p_embedding, p_capas, p_modulo, p_k);
    v_plan  json;
BEGIN
    PERFORM vec.configurar_busqueda('diseno');
    EXECUTE 'EXPLAIN (FORMAT JSON, VERBOSE'
            || CASE WHEN p_analizar THEN ', ANALYZE, BUFFERS' ELSE '' END
            || ') ' || cm.busqueda_hibrida_sql()
       INTO v_plan
      USING p_embedding, core.current_tenant(), v_capas, NULLIF(btrim(p_modulo), ''), p_k,
            COALESCE(p_incluir_sin_curar, false);
    RETURN v_plan::jsonb;
END $$;

COMMENT ON FUNCTION cm.explicar_busqueda_hibrida(vector, text[], text, integer, boolean, boolean) IS
'KC-CM-08-4: EXPLAIN (FORMAT JSON, VERBOSE[, ANALYZE]) de la consulta de cm.buscar_hibrido con los mismos parámetros y la misma configuración HNSW. Para pruebas y diagnóstico.';

-- ---------------------------------------------------------------------------
-- 6. Permisos
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION cm.concepto_curado(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION cm.capas_busqueda() FROM PUBLIC;
REVOKE ALL ON FUNCTION cm.validar_busqueda_hibrida(vector, text[], text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION cm.busqueda_hibrida_sql() FROM PUBLIC;
REVOKE ALL ON FUNCTION cm.buscar_hibrido(vector, text[], text, integer, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION cm.explicar_busqueda_hibrida(vector, text[], text, integer, boolean, boolean)
    FROM PUBLIC;

GRANT EXECUTE ON FUNCTION cm.concepto_curado(text, text) TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.capas_busqueda() TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.validar_busqueda_hibrida(vector, text[], text, integer)
    TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.busqueda_hibrida_sql() TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.buscar_hibrido(vector, text[], text, integer, boolean)
    TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION cm.explicar_busqueda_hibrida(vector, text[], text, integer, boolean, boolean)
    TO kallicode_app, kallicode_worker;
