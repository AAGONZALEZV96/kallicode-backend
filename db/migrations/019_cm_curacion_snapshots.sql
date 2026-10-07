-- ============================================================================
-- KALLICODE · 019_cm_curacion_snapshots.sql
-- KC-DB-04-3 (CM-F05) — Cola de curación y refresco por snapshot en lugar de
-- MERGE incremental.
--
-- El refresco diario con MERGE incremental dejaba nodos y aristas fantasma:
-- lo que desaparecía del repositorio nunca se borraba del grafo. Con snapshots:
--   1. el snapshot nuevo se carga en estado 'en_progreso' (la carga ya existe);
--      cada nodo/arista que sube queda marcado con su snapshot_id;
--   2. cm.publicar_snapshot() verifica recuentos y mueve el puntero
--      (cm.snapshot_vigente) en UNA transacción: ningún lector ve un estado
--      intermedio;
--   3. el snapshot anterior queda 'retirado' y cm.retirar_snapshot() borra sus
--      nodos, y por cascada sus aristas, de forma diferida.
-- Lo que sigue marcado con el snapshot viejo es justo lo que el nuevo no vio:
-- los fantasmas.
--
-- Además, toda arista o concepto con confianza < 0,75 se rechaza en su tabla y
-- se registra en cm.curation_queue (trigger).
--
-- Nota: el ticket nombra esta migración 011, pero 011 ya existe.
-- Nota: 'cargando' del ticket = estado 'en_progreso' del esquema vigente.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Estado 'retirado' para el snapshot sustituido.
-- ---------------------------------------------------------------------------
ALTER TABLE cm.repo_snapshots DROP CONSTRAINT snapshots_estado_chk;
ALTER TABLE cm.repo_snapshots ADD CONSTRAINT snapshots_estado_chk
    CHECK (estado IN ('en_progreso','completo','fallido','pendiente_intervencion','retirado'));

-- ---------------------------------------------------------------------------
-- 2. Marca de snapshot en los nodos y aristas que aún no la tenían
--    (cm.files ya la tiene; symbols y db_columns cuelgan de files y db_tables
--    por ON DELETE CASCADE). Nullable: lo ingerido antes de esta migración no
--    pertenece a ningún snapshot y la retirada nunca lo toca.
-- ---------------------------------------------------------------------------
ALTER TABLE cm.db_tables   ADD COLUMN snapshot_id text;
ALTER TABLE cm.definitions ADD COLUMN snapshot_id text;
ALTER TABLE cm.concepts    ADD COLUMN snapshot_id text;
ALTER TABLE cm.edges       ADD COLUMN snapshot_id text;

ALTER TABLE cm.db_tables ADD CONSTRAINT db_tables_snapshot_fkey
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id) NOT VALID;
ALTER TABLE cm.definitions ADD CONSTRAINT definitions_snapshot_fkey
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id) NOT VALID;
ALTER TABLE cm.concepts ADD CONSTRAINT concepts_snapshot_fkey
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id) NOT VALID;
ALTER TABLE cm.edges ADD CONSTRAINT edges_snapshot_fkey
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id) NOT VALID;

ALTER TABLE cm.db_tables   VALIDATE CONSTRAINT db_tables_snapshot_fkey;
ALTER TABLE cm.definitions VALIDATE CONSTRAINT definitions_snapshot_fkey;
ALTER TABLE cm.concepts    VALIDATE CONSTRAINT concepts_snapshot_fkey;
ALTER TABLE cm.edges       VALIDATE CONSTRAINT edges_snapshot_fkey;

CREATE INDEX files_snapshot_idx       ON cm.files       (tenant_id, snapshot_id);
CREATE INDEX db_tables_snapshot_idx   ON cm.db_tables   (tenant_id, snapshot_id);
CREATE INDEX definitions_snapshot_idx ON cm.definitions (tenant_id, snapshot_id);
CREATE INDEX concepts_snapshot_idx    ON cm.concepts    (tenant_id, snapshot_id);
CREATE INDEX edges_snapshot_idx       ON cm.edges       (tenant_id, snapshot_id);

-- ---------------------------------------------------------------------------
-- 3. Puntero al snapshot vigente: una fila por tenant.
-- ---------------------------------------------------------------------------
CREATE TABLE cm.snapshot_vigente (
    tenant_id      text PRIMARY KEY REFERENCES core.organizations(id),
    snapshot_id    text NOT NULL,
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON cm.snapshot_vigente TO kallicode_app;
ALTER TABLE cm.snapshot_vigente ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.snapshot_vigente FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.snapshot_vigente
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

-- ---------------------------------------------------------------------------
-- 4. Umbral de confianza y cola de curación (trigger).
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.umbral_confianza() RETURNS numeric
    LANGUAGE sql IMMUTABLE AS $$ SELECT 0.75::numeric $$;

-- Arista con confianza < umbral: no entra en cm.edges, queda pendiente de
-- revisión humana en cm.curation_queue (sin duplicar si ya estaba pendiente).
CREATE FUNCTION cm.edges_confianza_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    v_conf numeric;
BEGIN
    IF jsonb_typeof(NEW.props -> 'confidence') IS DISTINCT FROM 'number' THEN
        RETURN NEW;
    END IF;
    v_conf := (NEW.props ->> 'confidence')::numeric;
    IF v_conf >= cm.umbral_confianza() THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM cm.curation_queue
         WHERE tenant_id = NEW.tenant_id AND tipo = 'enlace' AND estado = 'pendiente'
           AND payload @> jsonb_build_object(
                 'tipo', NEW.tipo, 'src_id', NEW.src_id, 'dst_id', NEW.dst_id)
    ) THEN
        INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
        VALUES ('cq_' || replace(gen_random_uuid()::text, '-', ''), NEW.tenant_id, 'enlace',
                jsonb_build_object(
                    'tipo', NEW.tipo,
                    'src_kind', NEW.src_kind, 'src_id', NEW.src_id,
                    'dst_kind', NEW.dst_kind, 'dst_id', NEW.dst_id,
                    'confidence', v_conf, 'umbral', cm.umbral_confianza(),
                    'snapshot_id', NEW.snapshot_id));
    END IF;
    RETURN NULL;   -- rechazada: la fila no se inserta/actualiza
END $$;

CREATE TRIGGER edges_confianza
    BEFORE INSERT OR UPDATE OF props ON cm.edges
    FOR EACH ROW EXECUTE FUNCTION cm.edges_confianza_trg();

CREATE FUNCTION cm.concepts_confianza_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.confianza IS NULL OR NEW.confianza >= cm.umbral_confianza() THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM cm.curation_queue
         WHERE tenant_id = NEW.tenant_id AND tipo = 'concepto' AND estado = 'pendiente'
           AND payload @> jsonb_build_object('id', NEW.id)
    ) THEN
        INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
        VALUES ('cq_' || replace(gen_random_uuid()::text, '-', ''), NEW.tenant_id, 'concepto',
                jsonb_build_object(
                    'id', NEW.id, 'name_en', NEW.name_en, 'name_local', NEW.name_local,
                    'descripcion', NEW.descripcion,
                    'confidence', NEW.confianza, 'umbral', cm.umbral_confianza(),
                    'snapshot_id', NEW.snapshot_id));
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER concepts_confianza
    BEFORE INSERT OR UPDATE OF confianza ON cm.concepts
    FOR EACH ROW EXECUTE FUNCTION cm.concepts_confianza_trg();

-- ---------------------------------------------------------------------------
-- 5. Estadísticas del snapshot: nodos por kind y aristas por kind de origen.
--    Alimenta el informe de ingesta de CodeMapping.
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.stats_snapshot(p_snapshot text)
    RETURNS TABLE (kind text, nodos bigint, aristas bigint)
    LANGUAGE sql STABLE AS $$
    WITH n AS (
        SELECT 'file'::text AS k, count(*) AS c FROM cm.files WHERE snapshot_id = p_snapshot
        UNION ALL
        SELECT 'symbol', count(*) FROM cm.symbols s
          JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
         WHERE f.snapshot_id = p_snapshot
        UNION ALL
        SELECT 'db_table', count(*) FROM cm.db_tables WHERE snapshot_id = p_snapshot
        UNION ALL
        SELECT 'db_column', count(*) FROM cm.db_columns c
          JOIN cm.db_tables t ON t.tenant_id = c.tenant_id AND t.id = c.table_id
         WHERE t.snapshot_id = p_snapshot
        UNION ALL
        SELECT 'definition', count(*) FROM cm.definitions WHERE snapshot_id = p_snapshot
        UNION ALL
        SELECT 'concept', count(*) FROM cm.concepts WHERE snapshot_id = p_snapshot
    ), a AS (
        SELECT src_kind AS k, count(*) AS c FROM cm.edges
         WHERE snapshot_id = p_snapshot GROUP BY src_kind
    )
    SELECT n.k, n.c, coalesce(a.c, 0) FROM n LEFT JOIN a ON a.k = n.k ORDER BY n.k
$$;

-- ---------------------------------------------------------------------------
-- 6. Publicación atómica: verifica recuentos, marca 'completo' el nuevo, mueve
--    el puntero y marca 'retirado' el anterior, todo en la transacción del
--    llamador. Devuelve el id del snapshot sustituido (NULL si era el primero).
--    Si algo falla, nada cambia: el puntero sigue en el snapshot anterior.
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.publicar_snapshot(p_snapshot text, p_nodos bigint, p_aristas bigint)
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
    RETURN v_previo;
END $$;

-- ---------------------------------------------------------------------------
-- 7. Retirada diferida: borra los nodos de un snapshot 'retirado' y, por
--    cascada, sus aristas (las propias y cualquiera que apunte a un nodo ya
--    inexistente: las aristas son polimórficas y no tienen FK al nodo).
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm._nodo_existe(p_kind text, p_id text) RETURNS boolean
    LANGUAGE sql STABLE AS $$
    SELECT CASE p_kind
        WHEN 'file'       THEN EXISTS (SELECT 1 FROM cm.files       WHERE id = p_id)
        WHEN 'symbol'     THEN EXISTS (SELECT 1 FROM cm.symbols     WHERE id = p_id)
        WHEN 'db_table'   THEN EXISTS (SELECT 1 FROM cm.db_tables   WHERE id = p_id)
        WHEN 'db_column'  THEN EXISTS (SELECT 1 FROM cm.db_columns  WHERE id = p_id)
        WHEN 'definition' THEN EXISTS (SELECT 1 FROM cm.definitions WHERE id = p_id)
        WHEN 'concept'    THEN EXISTS (SELECT 1 FROM cm.concepts    WHERE id = p_id)
        ELSE false
    END
$$;

CREATE FUNCTION cm.retirar_snapshot(p_snapshot text)
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

    RETURN QUERY SELECT coalesce(v_nodos, 0), v_aristas;
END $$;

GRANT EXECUTE ON FUNCTION cm.stats_snapshot(text)                   TO kallicode_app;
GRANT EXECUTE ON FUNCTION cm.publicar_snapshot(text, bigint, bigint) TO kallicode_app;
GRANT EXECUTE ON FUNCTION cm.retirar_snapshot(text)                 TO kallicode_app;
