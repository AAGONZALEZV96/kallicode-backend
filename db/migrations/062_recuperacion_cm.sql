-- ============================================================================
-- KALLICODE · 062_recuperacion_cm.sql
-- KC-DB-11-4 (5 SP) — Recuperación del esquema cm y reconstrucción concurrente de los índices HNSW.
--
-- Continuación de KC-DB-11-1 (PITR): el PITR restaura TODO, pero no siempre es lo que hace falta. Un tenant cuyo
-- grafo se corrompió, o un backup que no llega a lo que se necesita, obligan a saber QUÉ se puede volver a producir
-- y qué NO (porque costó tokens de LLM, decisiones humanas o historia que no se puede rehacer).
--
-- 1. core.politica_recuperacion: la política escrita. Para cada tabla de cm y vec (y los tres índices HNSW):
--    'restaurar' (la copia de seguridad es la fuente), 'reconstruir' (se vuelve a producir desde su fuente) o
--    'mixta' (una parte y otra, con el detalle). core.tablas_sin_politica_recuperacion() lista las tablas de cm y vec
--    que nadie ha clasificado: una prueba exige que esté vacía, así que una tabla nueva obliga a decidir.
--
-- 2. cm.recuentos_grafo(tenant): recuentos por kind de nodo, por tipo de arista y de la tabla de alcanzabilidad.
--    Es lo que se compara antes y después de reconstruir.
--
-- 3. cm.vaciar_grafo_tenant(tenant, alcance, confirmar): borra el grafo de UN tenant para reconstruirlo.
--    'determinista' (archivos, símbolos, tablas y columnas del cliente, y sus aristas: se reproduce desde el repositorio
--    y la base del cliente sin LLM) o 'completo' (además definiciones, conceptos y aristas semánticas). NUNCA toca
--    incidentes, flujos de prueba, realimentación, cola de curación, nuggets ni snapshots: son historia, no derivados.
--    Exige repetir el tenant en `confirmar` para que no se ejecute por error. Solo kallicode_admin.
--
-- Aditiva y re-ejecutable. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ----------------------------------------------------------------------------
-- 1. Política de recuperación (tabla de sistema: sin tenant_id).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.politica_recuperacion (
    entidad     text PRIMARY KEY,
    tipo        text NOT NULL,
    estrategia  text NOT NULL,
    fuente      text NOT NULL,
    coste       text NOT NULL,
    detalle     text NOT NULL,
    CONSTRAINT politica_recuperacion_tipo_chk CHECK (tipo IN ('tabla', 'indice')),
    CONSTRAINT politica_recuperacion_estrategia_chk CHECK (estrategia IN ('restaurar', 'reconstruir', 'mixta')),
    CONSTRAINT politica_recuperacion_texto_chk CHECK (
        btrim(fuente) <> '' AND btrim(coste) <> '' AND btrim(detalle) <> ''
    )
);
COMMENT ON TABLE core.politica_recuperacion IS
'KC-DB-11-4: política escrita de recuperación por tabla/índice de cm y vec. restaurar = la copia de seguridad es la fuente; reconstruir = se vuelve a producir desde su fuente; mixta = una parte y otra (ver detalle). Documento: docs/recuperacion_cm_hnsw.md.';
REVOKE ALL ON core.politica_recuperacion FROM PUBLIC, kallicode_app, kallicode_worker;

INSERT INTO core.politica_recuperacion (entidad, tipo, estrategia, fuente, coste, detalle) VALUES
  -- Capa determinista: se reproduce desde el repositorio y la base del cliente, sin LLM.
  ('cm.files', 'tabla', 'reconstruir', 'repositorio (ingesta completa, fases 1-3)', 'minutos; sin LLM ni TEI', 'Un registro por archivo fuente con su sha256. Determinista: mismo commit, mismas filas.'),
  ('cm.symbols', 'tabla', 'reconstruir', 'repositorio (parser tree-sitter)', 'minutos; sin LLM ni TEI', 'Los ids son uuid5 deterministas: reingerir da los mismos ids, por lo que lo que cuelga de ellos (embeddings, enlaces) vuelve a enlazarse.'),
  ('cm.db_tables', 'tabla', 'reconstruir', 'base de datos del cliente (extract_schema)', 'segundos; consulta al catálogo del cliente', 'Requiere acceso a la base del cliente en el momento de reconstruir.'),
  ('cm.db_columns', 'tabla', 'reconstruir', 'base de datos del cliente (extract_schema)', 'segundos', 'Cuelga de cm.db_tables.'),
  ('cm.nodes', 'tabla', 'reconstruir', 'triggers nodes_sync_* sobre las seis tablas de nodos', 'nulo: se mantiene sola', 'Registro único de nodos; se regenera al reconstruir las tablas de nodos.'),
  ('cm.edges', 'tabla', 'mixta', 'DEFINES, CALLS, HAS_COLUMN, REFERENCES, USES_TABLE: repositorio y base del cliente; REALIZED_BY, RELATES, EXPRESSES: copia de seguridad', 'minutos las deterministas; tokens de LLM las semánticas', 'Las aristas semánticas salen de la fase de LLM y de la curación humana: no se reproducen igual, se restauran.'),
  ('cm.reachability', 'tabla', 'reconstruir', 'cm.edges (cm.refrescar_reachability)', 'segundos a minutos según el grafo', 'Cierre transitivo derivado; se invalida solo al cambiar las aristas y se vuelve a calcular.'),
  ('cm.reachability_estado', 'tabla', 'reconstruir', 'cm.refrescar_reachability', 'nulo', 'Vigencia de la tabla anterior.'),
  ('cm.edge_types', 'tabla', 'reconstruir', 'migraciones (002)', 'nulo', 'Catálogo sembrado por las migraciones.'),
  ('cm.node_types', 'tabla', 'reconstruir', 'migraciones (002)', 'nulo', 'Catálogo sembrado por las migraciones.'),
  -- Capa semántica y humana: costó tokens o decisiones; se restaura.
  ('cm.definitions', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'tokens de LLM (no determinista) + revisión humana', 'Descripciones generadas por LLM y curadas. Reconstruirlas cuesta dinero y no da el mismo texto.'),
  ('cm.concepts', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'tokens de LLM (no determinista) + revisión humana', 'Conceptos de negocio extraídos y curados.'),
  ('cm.nuggets', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'tokens de LLM', 'Hechos propuestos desde documentos; pendientes o aprobados por una persona.'),
  ('cm.curation_queue', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Decisiones humanas de curación: no existe otra fuente.'),
  ('cm.doc_chunk_links', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'minutos; depende de embeddings y de la fase documental', 'Enlaces entre fragmentos de documentos y nodos; se podrían recalcular, pero con resultados que dependen del modelo.'),
  -- Historia operativa: no se puede rehacer.
  ('cm.repo_snapshots', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Libro de ingestas: commits, fases, estados. Permite reanudar y auditar.'),
  ('cm.snapshot_vigente', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Qué snapshot es el vigente de cada tenant.'),
  ('cm.incidents', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Incidentes registrados: historia.'),
  ('cm.incident_nodes', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Nodos afectados por cada incidente.'),
  ('cm.test_flows', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Flujos de prueba registrados.'),
  ('cm.test_flow_covers', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Cobertura de los flujos de prueba.'),
  ('cm.realimentacion_tickets', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Realimentación del grafo desde tickets entregados (KC-CM-10-4).'),
  -- Vectores: reproducibles, pero caros; se restauran y se reparan por vigencia.
  ('vec.definition_embeddings', 'tabla', 'restaurar', 'copia de seguridad (PITR); alternativa: re-embedding con TEI (KC-CM-11-4)', 'inferencia BGE-M3 de cada nodo (horas a escala)', 'Borrar un nodo borra su embedding en cascada: reconstruir la capa determinista de un tenant invalida los embeddings de sus símbolos. Se restauran del backup o se recalculan con embeddings_a_reparar.'),
  ('vec.doc_chunks', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'ingesta de documentos + inferencia', 'Fragmentos de documentos del onboarding y sus vectores.'),
  ('vec.ticket_embeddings', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'inferencia por ticket', 'Embeddings de tickets.'),
  ('vec.incident_embeddings', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'inferencia por incidente', 'Embeddings de incidentes.'),
  ('vec.parametros_busqueda', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'reajuste manual', 'Parámetros de búsqueda ajustados (ef_search, etc.).'),
  ('vec.hnsw_metricas', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Medidas históricas de tamaño e hinchazón de los índices.'),
  ('vec.hnsw_catalogo', 'tabla', 'reconstruir', 'migraciones (044)', 'nulo', 'Definición de los tres índices HNSW; la siembran las migraciones.'),
  ('vec.acta_conversion_capa', 'tabla', 'restaurar', 'copia de seguridad (PITR)', 'no reproducible', 'Acta de la conversión de capas (KC-DB-04-4).'),
  -- Índices HNSW: siempre se reconstruyen.
  ('vec.ticket_embeddings_hnsw_ix', 'indice', 'reconstruir', 'vec.ticket_embeddings (CREATE/REINDEX CONCURRENTLY)', 'unos 80 min por millón de filas y ~4,5 KB de maintenance_work_mem por fila (docs/runbook-vectorial.md §7)', 'Un índice es derivado de su tabla: nunca se restaura por separado ni hace falta que la copia lo contenga en buen estado.'),
  ('vec.doc_chunks_hnsw_ix', 'indice', 'reconstruir', 'vec.doc_chunks (CREATE/REINDEX CONCURRENTLY)', 'ídem', 'Particionado por tenant: se reconstruye por partición.'),
  ('vec.definition_embeddings_hnsw_ix', 'indice', 'reconstruir', 'vec.definition_embeddings (CREATE/REINDEX CONCURRENTLY)', 'ídem', 'Particionado por tenant: se reconstruye por partición.')
ON CONFLICT (entidad) DO NOTHING;

CREATE OR REPLACE FUNCTION core.tablas_sin_politica_recuperacion()
RETURNS TABLE (tabla text)
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT t.tabla
      FROM core.tablas_retencion() t
     WHERE (t.tabla LIKE 'cm.%' OR t.tabla LIKE 'vec.%')
       AND NOT EXISTS (SELECT 1 FROM core.politica_recuperacion p WHERE p.entidad = t.tabla)
     ORDER BY t.tabla
$f$;
COMMENT ON FUNCTION core.tablas_sin_politica_recuperacion() IS
'KC-DB-11-4: tablas de cm y vec sin clasificar en core.politica_recuperacion (restaurar / reconstruir / mixta). Debe devolver cero filas: una prueba lo exige.';
REVOKE ALL ON FUNCTION core.tablas_sin_politica_recuperacion() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.tablas_sin_politica_recuperacion() TO kallicode_admin;

-- ----------------------------------------------------------------------------
-- 2. Recuentos del grafo de un tenant (lo que se compara antes y después de reconstruir).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cm.recuentos_grafo(p_tenant text)
RETURNS TABLE (objeto text, filas bigint)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT 'nodo:' || n.kind, count(*)::bigint FROM cm.nodes n WHERE n.tenant_id = p_tenant GROUP BY n.kind
    UNION ALL
    SELECT 'arista:' || e.tipo, count(*)::bigint FROM cm.edges e WHERE e.tenant_id = p_tenant GROUP BY e.tipo
    UNION ALL
    SELECT 'reachability', count(*)::bigint FROM cm.reachability r WHERE r.tenant_id = p_tenant
    ORDER BY 1
$f$;
COMMENT ON FUNCTION cm.recuentos_grafo(text) IS
'KC-DB-11-4: recuentos del grafo de un tenant por kind de nodo (nodo:<kind>), por tipo de arista (arista:<tipo>) y filas de cm.reachability. Solo kallicode_admin.';
REVOKE ALL ON FUNCTION cm.recuentos_grafo(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm.recuentos_grafo(text) TO kallicode_admin;

-- ----------------------------------------------------------------------------
-- 3. Vaciar el grafo de UN tenant para reconstruirlo.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cm.vaciar_grafo_tenant(
    p_tenant    text,
    p_alcance   text DEFAULT 'determinista',
    p_confirmar text DEFAULT NULL
) RETURNS TABLE (objeto text, filas bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    v_filas bigint;
BEGIN
    IF p_alcance NOT IN ('determinista', 'completo') THEN
        RAISE EXCEPTION 'vaciar_grafo_tenant: alcance % no válido (determinista | completo)', p_alcance
            USING ERRCODE = '22023';
    END IF;
    IF p_confirmar IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'vaciar_grafo_tenant: borra el grafo de %; repita el tenant en p_confirmar para confirmar', p_tenant
            USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.organizations o WHERE o.id = p_tenant) THEN
        RAISE EXCEPTION 'vaciar_grafo_tenant: el tenant % no existe', p_tenant USING ERRCODE = '22023';
    END IF;

    -- Se serializa con cualquier otra reconstrucción del mismo tenant.
    PERFORM pg_advisory_xact_lock(hashtextextended('cm.vaciar_grafo_tenant:' || p_tenant, 0));

    -- La alcanzabilidad es derivada en los dos alcances.
    DELETE FROM cm.reachability WHERE tenant_id = p_tenant;
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    objeto := 'cm.reachability'; filas := v_filas; RETURN NEXT;
    DELETE FROM cm.reachability_estado WHERE tenant_id = p_tenant;

    -- Aristas: las deterministas siempre; las semánticas solo en el alcance completo.
    DELETE FROM cm.edges
     WHERE tenant_id = p_tenant
       AND (tipo IN ('DEFINES', 'CALLS', 'HAS_COLUMN', 'REFERENCES', 'USES_TABLE')
            OR (p_alcance = 'completo' AND tipo IN ('REALIZED_BY', 'RELATES', 'EXPRESSES')));
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    objeto := 'cm.edges'; filas := v_filas; RETURN NEXT;

    IF p_alcance = 'completo' THEN
        DELETE FROM cm.definitions WHERE tenant_id = p_tenant;
        GET DIAGNOSTICS v_filas = ROW_COUNT;
        objeto := 'cm.definitions'; filas := v_filas; RETURN NEXT;
        DELETE FROM cm.concepts WHERE tenant_id = p_tenant;
        GET DIAGNOSTICS v_filas = ROW_COUNT;
        objeto := 'cm.concepts'; filas := v_filas; RETURN NEXT;
    END IF;

    -- Nodos de la capa determinista. Los triggers nodes_sync_del retiran su fila de cm.nodes y, por la FK en cascada,
    -- los embeddings de vec.definition_embeddings y los enlaces de cm.doc_chunk_links de esos nodos.
    DELETE FROM cm.db_tables WHERE tenant_id = p_tenant;   -- arrastra cm.db_columns
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    objeto := 'cm.db_tables'; filas := v_filas; RETURN NEXT;
    DELETE FROM cm.files WHERE tenant_id = p_tenant;       -- arrastra cm.symbols
    GET DIAGNOSTICS v_filas = ROW_COUNT;
    objeto := 'cm.files'; filas := v_filas; RETURN NEXT;
END
$f$;
COMMENT ON FUNCTION cm.vaciar_grafo_tenant(text, text, text) IS
'KC-DB-11-4: borra el grafo de UN tenant para reconstruirlo desde una ingesta completa. determinista = archivos, símbolos, tablas/columnas del cliente y sus aristas; completo = además definiciones, conceptos y aristas semánticas. No toca incidentes, flujos de prueba, realimentación, cola de curación, nuggets ni snapshots. Exige repetir el tenant en p_confirmar. Solo kallicode_admin. OJO: borra en cascada los embeddings de los nodos borrados (vec.definition_embeddings): hay que restaurarlos o recalcularlos.';
REVOKE ALL ON FUNCTION cm.vaciar_grafo_tenant(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cm.vaciar_grafo_tenant(text, text, text) TO kallicode_admin;

INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo) VALUES
    (NULL, 'core.politica_recuperacion', NULL, 'conservar',
     'Política escrita de recuperación (KC-DB-11-4): pocas filas, la cambia una migración, sin plazo propio.')
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;
