-- ============================================================================
-- KALLICODE · 059_cm_reachability.sql
-- KC-DB-11-2 — Límites de recorrido y cierre transitivo materializado en cm.reachability.
--
-- A 200.000 nodos el impacto a tres saltos sigue siendo viable con los índices de
-- KC-DB-04-2 (edges_in_ix / edges_out_ix), pero un nodo central sin límites recorre
-- el grafo entero: el LIMIT de la salida no acota el trabajo, porque el DISTINCT ON
-- obliga a evaluar el recorrido completo. Esta migración fija los tres requisitos de
-- todo recorrido recursivo del grafo y materializa el radio de impacto caliente:
--
--   1. Cláusula CYCLE en todo WITH RECURSIVE sobre cm.edges.
--   2. Política RLS trivialmente indexable: cm.nodes y cm.edges siguen con
--      exactamente tenant_id = core.current_tenant() (sin subconsultas).
--   3. Límite de profundidad (1..5, igual que K_MAX de kallicode_core.cm_impacto) y
--      de nodos visitados: el LIMIT se aplica DENTRO del recorrido, que PostgreSQL
--      evalúa nivel a nivel y bajo demanda, así que la recursión se detiene al
--      alcanzar el tope y se devuelve truncado = true (nunca un error ni un
--      resultado silenciosamente incompleto).
--
-- Objetos:
--   cm.reachability                 cierre transitivo a 1..3 saltos por las aristas de
--                                   dependencia (CALLS, HAS_COLUMN, REFERENCES, USES_TABLE)
--   cm.reachability_estado          una fila por tenant: snapshot y vigencia de la tabla
--   cm.refrescar_reachability()     reconstrucción del tenant de la sesión, con presupuesto
--   cm.reachability_invalidar_trg   toda escritura en cm.edges deja la tabla 'obsoleta'
--   cm.publicar_snapshot()          redefinida: refresca la tabla en su misma transacción
--   cm.retirar_snapshot()           redefinida: ídem tras borrar el snapshot retirado
--   cm.impacto()                    consulta de impacto/dependencias: usa la tabla si está
--                                   vigente y cae al CTE recursivo si no, con el mismo
--                                   resultado
--
-- La tabla refleja exactamente las aristas que ve el recorrido en vivo (todas las de
-- cm.edges del tenant, igual que las consultas de cm_impacto, que no filtran por
-- snapshot). Por eso cualquier escritura en cm.edges fuera del cambio de snapshot
-- (curación, realimentación) la invalida y la consulta vuelve al CTE hasta el
-- siguiente refresco.
--
-- Nota: el ticket nombra esta migración 044, pero 044 ya existe.
-- Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ---------------------------------------------------------------------------
-- 1. Tipos de arista materializados (los de dependencia, TIPOS_DEPENDENCIA de
--    kallicode_core.cm_impacto), ordenados en collation "C".
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.reachability_tipos() RETURNS text[]
    LANGUAGE sql IMMUTABLE AS $$
    SELECT ARRAY['CALLS', 'HAS_COLUMN', 'REFERENCES', 'USES_TABLE']::text[]
$$;

COMMENT ON FUNCTION cm.reachability_tipos() IS
'KC-DB-11-2: tipos de arista que cubre cm.reachability (los de dependencia), en orden "C". Una consulta con otros tipos usa el CTE recursivo.';

-- ---------------------------------------------------------------------------
-- 2. cm.reachability: src alcanza dst en `saltos` (mínimo) por aristas de dependencia.
--    Impacto de X  = filas con dst_id = X (índice inverso) -> via_src
--    Dependencias  = filas con src_id = X (PK)             -> via_dst
--    via_src / via_dst: tipo de la primera / última arista de un camino mínimo (el
--    menor en orden "C" si hay varios). Reproducen el via_tipo del recorrido en vivo,
--    que en impacto es la arista que sale del nodo alcanzado y en dependencias la que
--    entra en él.
-- ---------------------------------------------------------------------------
CREATE TABLE cm.reachability (
    tenant_id text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    src_id    text NOT NULL,
    dst_id    text NOT NULL,
    saltos    smallint NOT NULL,
    via_src   text NOT NULL,
    via_dst   text NOT NULL,
    PRIMARY KEY (tenant_id, src_id, dst_id),
    CONSTRAINT reach_saltos_chk CHECK (saltos BETWEEN 1 AND 3),
    CONSTRAINT reach_distintos_chk CHECK (src_id <> dst_id),
    CONSTRAINT reach_via_chk CHECK (
        via_src IN ('CALLS', 'HAS_COLUMN', 'REFERENCES', 'USES_TABLE') AND
        via_dst IN ('CALLS', 'HAS_COLUMN', 'REFERENCES', 'USES_TABLE'))
);

CREATE INDEX reachability_inversa_ix ON cm.reachability (tenant_id, dst_id, src_id)
    INCLUDE (saltos, via_src);

COMMENT ON TABLE cm.reachability IS
'KC-DB-11-2: cierre transitivo a 1..3 saltos de cm.edges por las aristas de dependencia. Derivada: se reconstruye en cm.publicar_snapshot / cm.retirar_snapshot (cm.refrescar_reachability) y solo se usa si cm.reachability_estado la marca vigente.';

ALTER TABLE cm.reachability ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.reachability FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.reachability
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
GRANT SELECT, INSERT, DELETE ON cm.reachability TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 3. Vigencia por tenant.
--    vigente   refleja cm.edges tal como estaba al refrescar, para snapshot_id
--    obsoleta  cm.edges cambió después del refresco
--    excedida  el recorrido superó el presupuesto de caminos: la tabla queda vacía
--              y el impacto usa siempre el CTE (nunca una tabla parcial)
-- ---------------------------------------------------------------------------
CREATE TABLE cm.reachability_estado (
    tenant_id     text PRIMARY KEY REFERENCES core.organizations(id) ON DELETE CASCADE,
    snapshot_id   text,
    estado        text NOT NULL,
    filas         bigint NOT NULL DEFAULT 0,
    caminos       bigint NOT NULL DEFAULT 0,
    presupuesto   bigint NOT NULL,
    duracion_ms   integer NOT NULL DEFAULT 0,
    refrescada_en timestamptz NOT NULL DEFAULT now(),
    invalidada_en timestamptz,
    CONSTRAINT reach_estado_chk CHECK (estado IN ('vigente', 'obsoleta', 'excedida')),
    CONSTRAINT reach_estado_contadores_chk
        CHECK (filas >= 0 AND caminos >= 0 AND presupuesto >= 1 AND duracion_ms >= 0),
    CONSTRAINT reach_estado_excedida_chk CHECK (estado <> 'excedida' OR filas = 0),
    CONSTRAINT reach_estado_invalidada_chk CHECK ((estado = 'obsoleta') = (invalidada_en IS NOT NULL))
);

COMMENT ON TABLE cm.reachability_estado IS
'KC-DB-11-2: vigencia de cm.reachability por tenant. cm.impacto solo usa la tabla con estado vigente y snapshot_id igual al de cm.snapshot_vigente.';

ALTER TABLE cm.reachability_estado ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.reachability_estado FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.reachability_estado
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON cm.reachability_estado TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 4. Refresco del tenant de la sesión.
--    Recorre hacia delante desde todo origen de una arista de dependencia, con CYCLE
--    (el origen va en la ruta, así que volver a él corta la rama igual que en el
--    recorrido en vivo). El LIMIT dentro del recorrido acota el trabajo: si los caminos
--    superan p_presupuesto_caminos, no se escribe nada y el estado queda 'excedida'.
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.refrescar_reachability(p_presupuesto_caminos bigint DEFAULT 5000000)
    RETURNS TABLE (estado text, filas bigint, caminos bigint)
    LANGUAGE plpgsql AS $$
#variable_conflict use_column
DECLARE
    v_tenant   text := core.current_tenant();
    v_snapshot text;
    v_inicio   timestamptz := clock_timestamp();
    v_caminos  bigint;
    v_filas    bigint := 0;
    v_estado   text;
BEGIN
    IF v_tenant IS NULL THEN
        RAISE EXCEPTION 'cm.refrescar_reachability requiere un tenant en la sesión'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_presupuesto_caminos IS NULL OR p_presupuesto_caminos < 1 THEN
        RAISE EXCEPTION 'presupuesto de caminos inválido: %', p_presupuesto_caminos
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Dos refrescos del mismo tenant se serializan.
    PERFORM pg_advisory_xact_lock(hashtextextended('cm.reachability:' || v_tenant, 0));

    SELECT sv.snapshot_id INTO v_snapshot FROM cm.snapshot_vigente sv
     WHERE sv.tenant_id = v_tenant;

    DELETE FROM cm.reachability rd WHERE rd.tenant_id = v_tenant;

    -- Una sola sentencia: `caminos` detiene la recursión en el presupuesto y la inserción
    -- solo escribe si no se superó (sin tablas temporales: kallicode_app no las necesita).
    WITH RECURSIVE recorrido (origen, id, saltos, via_src, via_dst) AS (
        SELECT DISTINCT eb.src_id, eb.src_id, 0, CAST(NULL AS text), CAST(NULL AS text)
          FROM cm.edges eb
         WHERE eb.tenant_id = v_tenant
           AND eb.tipo = ANY(cm.reachability_tipos())
        UNION ALL
        SELECT r.origen, er.dst_id, r.saltos + 1, coalesce(r.via_src, er.tipo), er.tipo
          FROM recorrido r
          JOIN cm.edges er
            ON er.tenant_id = v_tenant
           AND er.src_id = r.id
           AND er.tipo = ANY(cm.reachability_tipos())
         WHERE r.saltos < 3
    ) CYCLE id SET es_ciclo USING ruta,
    caminos AS MATERIALIZED (
        SELECT rc.origen, rc.id, rc.saltos, rc.via_src, rc.via_dst, rc.es_ciclo
          FROM recorrido rc
         LIMIT p_presupuesto_caminos + 1
    ),
    total AS (
        SELECT count(*) AS n FROM caminos
    ),
    escritas AS (
        INSERT INTO cm.reachability (tenant_id, src_id, dst_id, saltos, via_src, via_dst)
        SELECT v_tenant, c.origen, c.id, min(c.saltos),
               (array_agg(c.via_src ORDER BY c.saltos, c.via_src COLLATE "C"))[1],
               (array_agg(c.via_dst ORDER BY c.saltos, c.via_dst COLLATE "C"))[1]
          FROM caminos c
         WHERE NOT c.es_ciclo AND c.saltos > 0 AND c.origen <> c.id
           AND (SELECT t.n FROM total t) <= p_presupuesto_caminos
         GROUP BY c.origen, c.id
        RETURNING 1
    )
    SELECT (SELECT t.n FROM total t), (SELECT count(*) FROM escritas)
      INTO v_caminos, v_filas;

    v_estado := CASE WHEN v_caminos > p_presupuesto_caminos THEN 'excedida' ELSE 'vigente' END;

    INSERT INTO cm.reachability_estado AS e
           (tenant_id, snapshot_id, estado, filas, caminos, presupuesto, duracion_ms,
            refrescada_en, invalidada_en)
    VALUES (v_tenant, v_snapshot, v_estado, v_filas, v_caminos, p_presupuesto_caminos,
            CAST(extract(epoch FROM clock_timestamp() - v_inicio) * 1000 AS integer),
            now(), NULL)
    ON CONFLICT (tenant_id) DO UPDATE
       SET snapshot_id = EXCLUDED.snapshot_id, estado = EXCLUDED.estado,
           filas = EXCLUDED.filas, caminos = EXCLUDED.caminos,
           presupuesto = EXCLUDED.presupuesto, duracion_ms = EXCLUDED.duracion_ms,
           refrescada_en = EXCLUDED.refrescada_en, invalidada_en = NULL;

    RETURN QUERY SELECT v_estado, v_filas, v_caminos;
END $$;

COMMENT ON FUNCTION cm.refrescar_reachability(bigint) IS
'KC-DB-11-2: reconstruye cm.reachability del tenant de la sesión desde cm.edges (CYCLE, 3 saltos). Si los caminos superan el presupuesto deja la tabla vacía y el estado excedida. Devuelve estado, filas escritas y caminos recorridos.';

-- ---------------------------------------------------------------------------
-- 5. Invalidación: toda escritura en cm.edges deja 'obsoleta' la tabla de los
--    tenants afectados. Trigger por sentencia con tablas de transición (una por
--    evento: PostgreSQL no admite tablas de transición en triggers multi-evento).
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.reachability_invalidar_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        UPDATE cm.reachability_estado SET estado = 'obsoleta', invalidada_en = now()
         WHERE estado = 'vigente';
    ELSIF TG_OP = 'INSERT' THEN
        UPDATE cm.reachability_estado SET estado = 'obsoleta', invalidada_en = now()
         WHERE estado = 'vigente' AND tenant_id IN (SELECT n.tenant_id FROM nuevas n);
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE cm.reachability_estado SET estado = 'obsoleta', invalidada_en = now()
         WHERE estado = 'vigente' AND tenant_id IN (SELECT v.tenant_id FROM viejas v);
    ELSE
        UPDATE cm.reachability_estado SET estado = 'obsoleta', invalidada_en = now()
         WHERE estado = 'vigente'
           AND tenant_id IN (SELECT n.tenant_id FROM nuevas n
                             UNION SELECT v.tenant_id FROM viejas v);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER edges_reachability_ins
    AFTER INSERT ON cm.edges REFERENCING NEW TABLE AS nuevas
    FOR EACH STATEMENT EXECUTE FUNCTION cm.reachability_invalidar_trg();
CREATE TRIGGER edges_reachability_upd
    AFTER UPDATE ON cm.edges REFERENCING OLD TABLE AS viejas NEW TABLE AS nuevas
    FOR EACH STATEMENT EXECUTE FUNCTION cm.reachability_invalidar_trg();
CREATE TRIGGER edges_reachability_del
    AFTER DELETE ON cm.edges REFERENCING OLD TABLE AS viejas
    FOR EACH STATEMENT EXECUTE FUNCTION cm.reachability_invalidar_trg();
CREATE TRIGGER edges_reachability_trunc
    AFTER TRUNCATE ON cm.edges
    FOR EACH STATEMENT EXECUTE FUNCTION cm.reachability_invalidar_trg();

-- ---------------------------------------------------------------------------
-- 6. El refresco forma parte del cambio de snapshot. Cuerpos idénticos a 019 salvo la
--    llamada final a cm.refrescar_reachability(), en la misma transacción: ningún
--    lector ve el snapshot nuevo con la tabla del anterior.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cm.publicar_snapshot(p_snapshot text, p_nodos bigint, p_aristas bigint)
    RETURNS text
    LANGUAGE plpgsql AS $$
DECLARE
    v_tenant text := core.current_tenant();
    v_estado text;
    v_previo text;
    v_nodos  bigint;
    v_aristas bigint;
BEGIN
    SELECT estado INTO v_estado FROM cm.repo_snapshots
     WHERE tenant_id = v_tenant AND id = p_snapshot FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'snapshot % inexistente', p_snapshot USING ERRCODE = 'no_data_found';
    END IF;
    IF v_estado <> 'en_progreso' THEN
        RAISE EXCEPTION 'snapshot % en estado %, solo se publica desde en_progreso',
            p_snapshot, v_estado USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(sum(nodos), 0), coalesce(sum(aristas), 0) INTO v_nodos, v_aristas
      FROM cm.stats_snapshot(p_snapshot);
    IF v_nodos <> p_nodos OR v_aristas <> p_aristas THEN
        RAISE EXCEPTION 'recuentos del snapshot % no coinciden: cargados % nodos / % aristas, esperados % / %',
            p_snapshot, v_nodos, v_aristas, p_nodos, p_aristas USING ERRCODE = 'check_violation';
    END IF;

    SELECT snapshot_id INTO v_previo FROM cm.snapshot_vigente
     WHERE tenant_id = v_tenant FOR UPDATE;

    UPDATE cm.repo_snapshots SET estado = 'completo', finalizado_en = now()
     WHERE tenant_id = v_tenant AND id = p_snapshot;

    INSERT INTO cm.snapshot_vigente (tenant_id, snapshot_id) VALUES (v_tenant, p_snapshot)
    ON CONFLICT (tenant_id) DO UPDATE
        SET snapshot_id = EXCLUDED.snapshot_id, actualizado_en = now();

    IF v_previo IS NOT NULL AND v_previo <> p_snapshot THEN
        UPDATE cm.repo_snapshots SET estado = 'retirado'
         WHERE tenant_id = v_tenant AND id = v_previo;
    ELSE
        v_previo := NULL;
    END IF;

    -- KC-DB-11-2: cierre transitivo del grafo recién publicado.
    PERFORM * FROM cm.refrescar_reachability();
    RETURN v_previo;
END $$;

CREATE OR REPLACE FUNCTION cm.retirar_snapshot(p_snapshot text)
    RETURNS TABLE (nodos_borrados bigint, aristas_borradas bigint)
    LANGUAGE plpgsql AS $$
DECLARE
    v_tenant  text := core.current_tenant();
    v_estado  text;
    v_nodos   bigint := 0;
    v_aristas bigint := 0;
    v_n       bigint;
BEGIN
    SELECT estado INTO v_estado FROM cm.repo_snapshots
     WHERE tenant_id = v_tenant AND id = p_snapshot FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'snapshot % inexistente', p_snapshot USING ERRCODE = 'no_data_found';
    END IF;
    IF v_estado <> 'retirado' THEN
        RAISE EXCEPTION 'snapshot % en estado %, solo se retira un snapshot ya sustituido',
            p_snapshot, v_estado USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Cuenta antes de borrar: files y db_tables arrastran symbols y db_columns.
    SELECT sum(nodos) INTO v_nodos FROM cm.stats_snapshot(p_snapshot);

    DELETE FROM cm.edges WHERE tenant_id = v_tenant AND snapshot_id = p_snapshot;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_aristas := v_n;

    DELETE FROM cm.concepts    WHERE tenant_id = v_tenant AND snapshot_id = p_snapshot;
    DELETE FROM cm.definitions WHERE tenant_id = v_tenant AND snapshot_id = p_snapshot;
    DELETE FROM cm.db_tables   WHERE tenant_id = v_tenant AND snapshot_id = p_snapshot;
    DELETE FROM cm.files       WHERE tenant_id = v_tenant AND snapshot_id = p_snapshot;

    -- Aristas de otros snapshots que colgaban de un nodo recién borrado.
    DELETE FROM cm.edges
     WHERE tenant_id = v_tenant
       AND NOT (cm._nodo_existe(src_kind, src_id) AND cm._nodo_existe(dst_kind, dst_id));
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_aristas := v_aristas + v_n;

    -- KC-DB-11-2: el borrado invalidó la tabla; se reconstruye sin el snapshot retirado.
    PERFORM * FROM cm.refrescar_reachability();

    RETURN QUERY SELECT coalesce(v_nodos, 0), v_aristas;
END $$;

-- ---------------------------------------------------------------------------
-- 7. Consulta de impacto con límites.
--    p_direccion  entrante (impacto: quién depende del área) | saliente (de qué
--                 depende) | ambas (vecindad; siempre por el CTE)
--    p_ruta       auto | materializada | recursiva. auto usa la tabla si está vigente
--                 para el snapshot actual, k <= 3, el sentido es uno solo y los tipos
--                 son exactamente los de cm.reachability_tipos(); en otro caso el CTE.
--                 materializada falla si la tabla no se puede usar.
--    p_max_visitados  tope de visitas del recorrido (filas del CTE, semillas incluidas)
--                 o de nodos de la tabla. Superado: se devuelven los más cercanos y
--                 truncado = true en todas las filas.
--    Semillas y orden idénticos a kallicode_core.cm_impacto (SQL_IMPACTO /
--    SQL_DEPENDENCIAS): cada nodo una vez, distancia mínima, via_tipo menor en "C",
--    orden distancia, id (C). fuente = materializada | recursiva.
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.impacto(
    p_nodos         text[]  DEFAULT '{}',
    p_ficheros      text[]  DEFAULT '{}',
    p_k             integer DEFAULT 3,
    p_tipos         text[]  DEFAULT cm.reachability_tipos(),
    p_direccion     text    DEFAULT 'entrante',
    p_max_visitados integer DEFAULT 5000,
    p_ruta          text    DEFAULT 'auto'
)
    RETURNS TABLE (id text, kind text, distancia integer, via_tipo text, fuente text,
                   truncado boolean)
    LANGUAGE plpgsql STABLE AS $$
#variable_conflict use_column
DECLARE
    v_tabla boolean;
BEGIN
    IF p_k IS NULL OR p_k < 1 OR p_k > 5 THEN
        RAISE EXCEPTION 'k fuera de rango (1..5): %', p_k USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_max_visitados IS NULL OR p_max_visitados < 1 OR p_max_visitados > 100000 THEN
        RAISE EXCEPTION 'max_visitados fuera de rango (1..100000): %', p_max_visitados
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_direccion IS NULL OR p_direccion NOT IN ('entrante', 'saliente', 'ambas') THEN
        RAISE EXCEPTION 'dirección inválida: %', p_direccion USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_ruta IS NULL OR p_ruta NOT IN ('auto', 'materializada', 'recursiva') THEN
        RAISE EXCEPTION 'ruta inválida: %', p_ruta USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_tipos IS NULL OR cardinality(p_tipos) = 0 OR EXISTS (
        SELECT 1 FROM unnest(p_tipos) t(tipo)
         WHERE t.tipo IS NULL OR t.tipo NOT IN ('DEFINES', 'CALLS', 'HAS_COLUMN', 'REFERENCES',
                                                'USES_TABLE', 'REALIZED_BY', 'RELATES', 'EXPRESSES'))
    THEN
        RAISE EXCEPTION 'tipos de arista inválidos: %', p_tipos USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_tabla := p_ruta <> 'recursiva'
        AND p_direccion <> 'ambas'
        AND p_k <= 3
        AND (SELECT array_agg(DISTINCT t.tipo COLLATE "C" ORDER BY t.tipo COLLATE "C")
               FROM unnest(p_tipos) t(tipo)) = cm.reachability_tipos()
        AND EXISTS (
            SELECT 1
              FROM cm.reachability_estado re
              LEFT JOIN cm.snapshot_vigente sv
                ON sv.tenant_id = core.current_tenant() AND sv.tenant_id = re.tenant_id
             WHERE re.tenant_id = core.current_tenant()
               AND re.estado = 'vigente'
               AND re.snapshot_id IS NOT DISTINCT FROM sv.snapshot_id);

    IF p_ruta = 'materializada' AND NOT v_tabla THEN
        RAISE EXCEPTION 'cm.reachability no es utilizable para esta consulta (estado, k, sentido o tipos)'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;

    IF v_tabla THEN
        RETURN QUERY
        WITH semillas_fichero AS (
            SELECT sf.id
              FROM cm.files sf
             WHERE sf.tenant_id = core.current_tenant()
               AND sf.path = ANY(p_ficheros)
        ),
        semillas AS (
            SELECT sn.id
              FROM cm.nodes sn
             WHERE sn.tenant_id = core.current_tenant()
               AND sn.id = ANY(p_nodos)
            UNION
            SELECT x.id FROM semillas_fichero x
            UNION
            SELECT ss.id
              FROM cm.symbols ss
              JOIN semillas_fichero y ON y.id = ss.file_id
             WHERE ss.tenant_id = core.current_tenant()
        ),
        pares AS (
            SELECT ri.src_id AS id, ri.saltos, ri.via_src AS via
              FROM cm.reachability ri
             WHERE p_direccion = 'entrante'
               AND ri.tenant_id = core.current_tenant()
               AND ri.dst_id IN (SELECT s.id FROM semillas s)
               AND ri.saltos <= p_k
            UNION ALL
            SELECT ro.dst_id, ro.saltos, ro.via_dst
              FROM cm.reachability ro
             WHERE p_direccion = 'saliente'
               AND ro.tenant_id = core.current_tenant()
               AND ro.src_id IN (SELECT s.id FROM semillas s)
               AND ro.saltos <= p_k
        ),
        alcanzados AS (
            SELECT s.id, 0 AS distancia, CAST(NULL AS text) AS via_tipo FROM semillas s
            UNION ALL
            (SELECT DISTINCT ON (p.id) p.id, CAST(p.saltos AS integer), p.via
               FROM pares p
              WHERE NOT EXISTS (SELECT 1 FROM semillas s2 WHERE s2.id = p.id)
              ORDER BY p.id, p.saltos, p.via COLLATE "C")
        ),
        resultado AS MATERIALIZED (
            SELECT a.id, n.kind, a.distancia, a.via_tipo
              FROM alcanzados a
              JOIN cm.nodes n ON n.tenant_id = core.current_tenant() AND n.id = a.id
             ORDER BY a.distancia, a.id COLLATE "C"
             LIMIT p_max_visitados + 1
        )
        SELECT r.id, r.kind, r.distancia, r.via_tipo, 'materializada'::text,
               (SELECT count(*) FROM resultado) > p_max_visitados
          FROM resultado r
         ORDER BY r.distancia, r.id COLLATE "C"
         LIMIT p_max_visitados;
        RETURN;
    END IF;

    -- Ruta recursiva: el LIMIT de `visitas` detiene la recursión (se evalúa nivel a nivel
    -- y bajo demanda), así que el trabajo queda acotado aunque el área sea un nodo central.
    RETURN QUERY
    WITH RECURSIVE semillas_fichero AS (
        SELECT sf.id
          FROM cm.files sf
         WHERE sf.tenant_id = core.current_tenant()
           AND sf.path = ANY(p_ficheros)
    ),
    semillas AS (
        SELECT sn.id
          FROM cm.nodes sn
         WHERE sn.tenant_id = core.current_tenant()
           AND sn.id = ANY(p_nodos)
        UNION
        SELECT x.id FROM semillas_fichero x
        UNION
        SELECT ss.id
          FROM cm.symbols ss
          JOIN semillas_fichero y ON y.id = ss.file_id
         WHERE ss.tenant_id = core.current_tenant()
    ),
    recorrido (id, distancia, via_tipo) AS (
        SELECT s.id, 0, CAST(NULL AS text) FROM semillas s
        UNION ALL
        SELECT v.id, r.distancia + 1, v.tipo
          FROM recorrido r
          CROSS JOIN LATERAL (
              SELECT ei.src_id AS id, ei.tipo
                FROM cm.edges ei
               WHERE p_direccion IN ('entrante', 'ambas')
                 AND ei.tenant_id = core.current_tenant()
                 AND ei.dst_id = r.id
                 AND ei.tipo = ANY(p_tipos)
              UNION ALL
              SELECT eo.dst_id AS id, eo.tipo
                FROM cm.edges eo
               WHERE p_direccion IN ('saliente', 'ambas')
                 AND eo.tenant_id = core.current_tenant()
                 AND eo.src_id = r.id
                 AND eo.tipo = ANY(p_tipos)
          ) v
         WHERE r.distancia < p_k
    ) CYCLE id SET es_ciclo USING ruta,
    visitas AS MATERIALIZED (
        SELECT rc.id, rc.distancia, rc.via_tipo, rc.es_ciclo
          FROM recorrido rc
         LIMIT p_max_visitados + 1
    ),
    alcanzados AS (
        SELECT DISTINCT ON (vi.id) vi.id, vi.distancia, vi.via_tipo
          FROM visitas vi
         WHERE NOT vi.es_ciclo
         ORDER BY vi.id, vi.distancia, vi.via_tipo COLLATE "C" NULLS FIRST
    )
    SELECT a.id, n.kind, a.distancia, a.via_tipo, 'recursiva'::text,
           (SELECT count(*) FROM visitas) > p_max_visitados
      FROM alcanzados a
      JOIN cm.nodes n ON n.tenant_id = core.current_tenant() AND n.id = a.id
     ORDER BY a.distancia, a.id COLLATE "C"
     LIMIT p_max_visitados;
END $$;

COMMENT ON FUNCTION cm.impacto(text[], text[], integer, text[], text, integer, text) IS
'KC-DB-11-2: impacto (entrante) o dependencias (saliente) a k saltos con límite de visitas. Usa cm.reachability si está vigente y cae al CTE recursivo (CYCLE) si no, con el mismo resultado; truncado = true si se alcanzó p_max_visitados.';

GRANT EXECUTE ON FUNCTION cm.reachability_tipos()                                      TO kallicode_app;
GRANT EXECUTE ON FUNCTION cm.refrescar_reachability(bigint)                            TO kallicode_app;
GRANT EXECUTE ON FUNCTION cm.impacto(text[], text[], integer, text[], text, integer, text) TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 8. Retención (V26 / informe de KC-DB-10-4): tablas derivadas del grafo, sin plazo
--    propio; se reconstruyen en cada snapshot y se eliminan con su tenant (ON DELETE
--    CASCADE desde core.organizations).
-- ---------------------------------------------------------------------------
INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo) VALUES
    (NULL, 'cm.reachability', NULL, 'conservar',
     'Cierre transitivo derivado de cm.edges (KC-DB-11-2): se reconstruye entero en cada cambio de snapshot y se elimina con su tenant. Sin plazo propio.'),
    (NULL, 'cm.reachability_estado', NULL, 'conservar',
     'Vigencia de cm.reachability por tenant (KC-DB-11-2): una fila por tenant, se reescribe en cada refresco y se elimina con su tenant.')
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;
