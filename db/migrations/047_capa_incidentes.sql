-- ============================================================================
-- KALLICODE · 047_capa_incidentes.sql
-- KC-CM-09-2 — Capa de incidentes: memoria de fallos del cliente y resolución de {{incidents}}.
--
-- cm.incidents existe desde 002 (PK (tenant_id, id), RLS activa y forzada por el bucle de 002,
-- ticket_id -> core.tickets compuesta desde 014-016) pero sin escritores ni lectores, con los
-- nodos afectados solo como jsonb. Esta migración la deja lista para el registro y la consulta
-- por área:
--
--   1. cm.incidents
--        origen          ticket | postmortem | manual. 'ticket' exige ticket_id. Por defecto
--                        'manual' para no romper altas existentes sin ticket.
--        evidencia_ref   referencia a la evidencia (chunk del postmortem, ruta del expediente);
--                        sin FK: la evidencia puede purgarse y el incidente debe sobrevivir.
--        actualizado_en  última vez que se registró de nuevo (registro idempotente por ticket).
--        Cotas de longitud de resumen, causa y corrección (el resolutor recorta por registros
--        enteros y necesita que cada uno quepa en un presupuesto razonable) y
--        nodos_afectados como array jsonb (foto de los ids al registrar).
--        Único (tenant_id, ticket_id): un ticket deja un solo incidente.
--   2. cm.incident_nodes   enlaces incidente -> nodo del grafo (FK a cm.nodes en cascada: si un
--                          refresco retira el nodo se pierde el enlace, no el incidente, que
--                          conserva la foto en nodos_afectados). Índice por (tenant_id, nodo_ref)
--                          para la consulta por área.
--   3. vec.incident_embeddings  embedding BGE-M3 (1024) del texto del incidente para ordenar por
--                          relevancia semántica. Sin HNSW: el resolutor solo compara los
--                          candidatos ya acotados por el grafo (distancia exacta); un índice no se
--                          usaría y exigiría alta en vec.hnsw_catalogo y su REINDEX programado.
--   RLS activa y FORZADA en las dos tablas nuevas, con la política tenant_isolation de cm.*.
--
-- No es destructiva: ADD COLUMN con default, ADD CONSTRAINT, CREATE TABLE/INDEX. Si alguna fila
-- previa viola una cota, la migración entera se revierte sin tocar nada.
-- Sin BEGIN/COMMIT: el runner abre la transacción (db/migrations/README.md).
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;   -- dueño de cm.* y vec.* (test_roles: el esquema es de kallicode_admin)

-- ---------------------------------------------------------------------------
-- 1. cm.incidents
-- ---------------------------------------------------------------------------
ALTER TABLE cm.incidents
    ADD COLUMN origen         text NOT NULL DEFAULT 'manual',
    ADD COLUMN evidencia_ref  text,
    ADD COLUMN actualizado_en timestamptz NOT NULL DEFAULT now();

ALTER TABLE cm.incidents
    ADD CONSTRAINT incidents_origen_chk
        CHECK (origen IN ('ticket', 'postmortem', 'manual')),
    ADD CONSTRAINT incidents_origen_ticket_chk
        CHECK (origen <> 'ticket' OR ticket_id IS NOT NULL),
    ADD CONSTRAINT incidents_resumen_len_chk
        CHECK (char_length(btrim(resumen)) BETWEEN 1 AND 500),
    ADD CONSTRAINT incidents_causa_len_chk
        CHECK (causa IS NULL OR char_length(causa) <= 1000),
    ADD CONSTRAINT incidents_correccion_len_chk
        CHECK (correccion IS NULL OR char_length(correccion) <= 1000),
    ADD CONSTRAINT incidents_evidencia_len_chk
        CHECK (evidencia_ref IS NULL OR char_length(evidencia_ref) <= 500),
    ADD CONSTRAINT incidents_nodos_array_chk
        CHECK (jsonb_typeof(nodos_afectados) = 'array');

CREATE UNIQUE INDEX incidents_ticket_uq
    ON cm.incidents (tenant_id, ticket_id)
    WHERE ticket_id IS NOT NULL;

COMMENT ON TABLE cm.incidents IS
'KC-CM-09-2: memoria de fallos del cliente. Un incidente por ticket (incidents_ticket_uq); enlaces a nodos en cm.incident_nodes; embedding en vec.incident_embeddings. Lo resuelve {{incidents}} (kallicode_core.cm_incidentes).';

-- ---------------------------------------------------------------------------
-- 2. cm.incident_nodes
-- ---------------------------------------------------------------------------
CREATE TABLE cm.incident_nodes (
    tenant_id   text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    incident_id text NOT NULL,
    nodo_ref    text NOT NULL,
    creado_en   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, incident_id, nodo_ref),
    CONSTRAINT incn_incidente_fk FOREIGN KEY (tenant_id, incident_id)
        REFERENCES cm.incidents (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT incn_nodo_fk FOREIGN KEY (tenant_id, nodo_ref)
        REFERENCES cm.nodes (tenant_id, id) ON DELETE CASCADE
);
-- Consulta por área (nodo -> incidentes) y cobertura de la FK incn_nodo_fk.
CREATE INDEX incident_nodes_nodo_ix ON cm.incident_nodes (tenant_id, nodo_ref);

ALTER TABLE cm.incident_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm.incident_nodes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm.incident_nodes
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON cm.incident_nodes TO kallicode_app, kallicode_worker;

-- ---------------------------------------------------------------------------
-- 3. vec.incident_embeddings
-- ---------------------------------------------------------------------------
CREATE TABLE vec.incident_embeddings (
    tenant_id        text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    incident_id      text NOT NULL,
    embedding        vector(1024) NOT NULL,
    modelo_embedding text NOT NULL DEFAULT 'bge-m3',
    texto_hash       text NOT NULL,                  -- re-embeber solo si cambió el texto
    creado_en        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, incident_id),
    CONSTRAINT incemb_incidente_fk FOREIGN KEY (tenant_id, incident_id)
        REFERENCES cm.incidents (tenant_id, id) ON DELETE CASCADE
);

ALTER TABLE vec.incident_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE vec.incident_embeddings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vec.incident_embeddings
    USING (tenant_id = core.current_tenant())
    WITH CHECK (tenant_id = core.current_tenant());
GRANT SELECT, INSERT, UPDATE, DELETE ON vec.incident_embeddings TO kallicode_app, kallicode_worker;
