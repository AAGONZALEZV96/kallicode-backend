-- ============================================================================
-- KALLICODE · 022_vec_fk_cm.sql
-- KC-DB-04-4 (DB-F18) — Consolidación de vec.definition_embeddings contra cm.nodes.
--
-- vec.definition_embeddings.nodo_ref era un text libre documentado como «id del
-- nodo en el grafo (Neo4j)», y de_capa_chk restringía capa a
-- ('definicion','negocio','codigo'), tres valores que no corresponden a ningún
-- kind del grafo real. Con cm desplegado (KC-ONB-03-1, 002_esquema_cm.sql):
--
--   1. cm.nodes(tenant_id, id, kind): registro único de nodos del grafo. Los
--      nodos viven en seis tablas (files, symbols, db_tables, db_columns,
--      definitions, concepts); una FK no puede apuntar a seis tablas, así que
--      cm.nodes se mantiene sincronizada con triggers y es el destino de la FK.
--   2. capa pasa a ser el kind del nodo (catálogo cm.node_types) y los datos
--      existentes se convierten, dejando constancia en vec.acta_conversion_capa.
--   3. nodo_ref pasa a FK compuesta (tenant_id, nodo_ref) -> cm.nodes(tenant_id, id)
--      con ON DELETE CASCADE: borrar un nodo borra su embedding en la misma
--      transacción, y ningún embedding puede apuntar a un nodo de otro tenant.
--
-- Nota: el ticket nombra esta migración 012, pero 012 ya existe.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. cm.nodes: un registro por nodo, con su kind.
-- ---------------------------------------------------------------------------
CREATE TABLE cm.nodes (
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    id        text NOT NULL,
    kind      text NOT NULL REFERENCES cm.node_types(kind),
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX nodes_kind_idx ON cm.nodes (tenant_id, kind);

GRANT SELECT, INSERT, UPDATE, DELETE ON cm.nodes TO kallicode_app;
ALTER TABLE cm.nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.nodes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.nodes
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());

-- Sincronización: cada fila de una tabla de nodos registra/retira su nodo.
-- TG_ARGV[0] es el kind. Un id repetido entre kinds distintos viola la PK y
-- aborta la escritura (el id de un nodo es único por tenant, sea cual sea su kind).
CREATE FUNCTION cm.nodes_sync_ins_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cm.nodes (tenant_id, id, kind) VALUES (NEW.tenant_id, NEW.id, TG_ARGV[0]);
    RETURN NULL;
END $$;

CREATE FUNCTION cm.nodes_sync_del_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM cm.nodes
     WHERE tenant_id = OLD.tenant_id AND id = OLD.id AND kind = TG_ARGV[0];
    RETURN NULL;
END $$;

CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.files
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('file');
CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.symbols
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('symbol');
CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.db_tables
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('db_table');
CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.db_columns
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('db_column');
CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.definitions
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('definition');
CREATE TRIGGER nodes_sync_ins AFTER INSERT ON cm.concepts
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_ins_trg('concept');

CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.files
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('file');
CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.symbols
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('symbol');
CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.db_tables
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('db_table');
CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.db_columns
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('db_column');
CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.definitions
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('definition');
CREATE TRIGGER nodes_sync_del AFTER DELETE ON cm.concepts
    FOR EACH ROW EXECUTE FUNCTION cm.nodes_sync_del_trg('concept');

-- Relleno de lo ya cargado (el runner corre como kallicode_admin, BYPASSRLS).
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'file'       FROM cm.files;
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'symbol'     FROM cm.symbols;
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'db_table'   FROM cm.db_tables;
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'db_column'  FROM cm.db_columns;
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'definition' FROM cm.definitions;
INSERT INTO cm.nodes (tenant_id, id, kind) SELECT tenant_id, id, 'concept'    FROM cm.concepts;

-- ---------------------------------------------------------------------------
-- 2. Acta de conversión de capa -> kind. Solo la lee el admin (sin GRANT al app).
--    Cada fila resume un par (capa antigua -> kind nuevo). Los embeddings cuyo
--    nodo_ref no existe en cm.nodes no tienen a qué convertirse: se eliminan
--    (son datos derivados, el siguiente refresco los regenera) y quedan
--    listados en ids_huerfanos.
-- ---------------------------------------------------------------------------
CREATE TABLE vec.acta_conversion_capa (
    id               bigserial PRIMARY KEY,
    migracion        text NOT NULL,
    capa_antigua     text NOT NULL,
    capa_nueva       text,                       -- NULL en la fila de huérfanos
    filas_convertidas bigint NOT NULL DEFAULT 0,
    filas_huerfanas  bigint NOT NULL DEFAULT 0,
    ids_huerfanos    jsonb NOT NULL DEFAULT '[]',
    ejecutado_en     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE vec.definition_embeddings DROP CONSTRAINT de_capa_chk;

INSERT INTO vec.acta_conversion_capa (migracion, capa_antigua, capa_nueva, filas_convertidas)
SELECT '022_vec_fk_cm.sql', d.capa, n.kind, count(*)
  FROM vec.definition_embeddings d
  JOIN cm.nodes n ON n.tenant_id = d.tenant_id AND n.id = d.nodo_ref
 GROUP BY d.capa, n.kind;

INSERT INTO vec.acta_conversion_capa (migracion, capa_antigua, filas_huerfanas, ids_huerfanos)
SELECT '022_vec_fk_cm.sql', d.capa, count(*),
       jsonb_agg(jsonb_build_object('tenant_id', d.tenant_id, 'id', d.id, 'nodo_ref', d.nodo_ref))
  FROM vec.definition_embeddings d
 WHERE NOT EXISTS (SELECT 1 FROM cm.nodes n WHERE n.tenant_id = d.tenant_id AND n.id = d.nodo_ref)
 GROUP BY d.capa;

UPDATE vec.definition_embeddings d
   SET capa = n.kind
  FROM cm.nodes n
 WHERE n.tenant_id = d.tenant_id AND n.id = d.nodo_ref;

DELETE FROM vec.definition_embeddings d
 WHERE NOT EXISTS (SELECT 1 FROM cm.nodes n WHERE n.tenant_id = d.tenant_id AND n.id = d.nodo_ref);

-- ---------------------------------------------------------------------------
-- 3. Restricciones nuevas. capa admite exactamente los kinds del grafo
--    (cm.node_types); nodo_ref es FK compuesta a cm.nodes. La UNIQUE
--    (tenant_id, nodo_ref) existente (de_nodo_uq) respalda la FK en el lado hijo.
-- ---------------------------------------------------------------------------
ALTER TABLE vec.definition_embeddings ADD CONSTRAINT de_capa_chk
    CHECK (capa IN ('file','symbol','db_table','db_column','definition','concept'));

ALTER TABLE vec.definition_embeddings ADD CONSTRAINT de_nodo_fk
    FOREIGN KEY (tenant_id, nodo_ref) REFERENCES cm.nodes (tenant_id, id)
    ON DELETE CASCADE NOT VALID;
ALTER TABLE vec.definition_embeddings VALIDATE CONSTRAINT de_nodo_fk;

COMMENT ON COLUMN vec.definition_embeddings.nodo_ref IS
    'id del nodo en cm.nodes (FK compuesta con tenant_id, ON DELETE CASCADE)';
COMMENT ON COLUMN vec.definition_embeddings.capa IS
    'kind del nodo: file | symbol | db_table | db_column | definition | concept';
