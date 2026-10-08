-- Migración 021: Catálogo generado de tipos de nodo y arista (KC-DB-04-6)
-- Formaliza el metamodelo del grafo de CodeMapping cm.* en tablas de catálogo y vistas de consulta.

SET lock_timeout = '5s';
SET statement_timeout = '30s';


-- 1. Catálogo canónico de tipos de nodo
CREATE TABLE IF NOT EXISTS cm.node_types (
    kind            text PRIMARY KEY,
    categoria       text NOT NULL, -- 'codigo', 'base_datos', 'semantica'
    descripcion     text NOT NULL,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cm.node_types (kind, categoria, descripcion) VALUES
    ('file', 'codigo', 'Archivo fuente indexado en el repositorio'),
    ('symbol', 'codigo', 'Símbolo del AST: función, clase, método, interfaz, enum o tipo'),
    ('db_table', 'base_datos', 'Tabla relacional de esquema SQL analizado'),
    ('db_column', 'base_datos', 'Columna relacional con atributos y tipos'),
    ('definition', 'semantica', 'Unidad de definición semántica vectorizada'),
    ('concept', 'semantica', 'Concepto de dominio o término de ontología')
ON CONFLICT (kind) DO NOTHING;

-- 2. Catálogo canónico de tipos de arista
CREATE TABLE IF NOT EXISTS cm.edge_types (
    tipo            text PRIMARY KEY,
    es_dirigida     boolean NOT NULL DEFAULT true,
    descripcion     text NOT NULL,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cm.edge_types (tipo, es_dirigida, descripcion) VALUES
    ('DEFINES', true, 'El archivo u objeto define un símbolo o entidad'),
    ('CALLS', true, 'Invocación o referencia de llamada entre símbolos'),
    ('HAS_COLUMN', true, 'Pertenencia estructural de una columna a una tabla'),
    ('REFERENCES', true, 'Referencia foránea o dependencia entre tablas/entidades'),
    ('USES_TABLE', true, 'Símbolo de código ejecuta lecturas o escrituras sobre tabla SQL'),
    ('REALIZED_BY', true, 'Concepto de negocio materializado en código o tabla'),
    ('RELATES', false, 'Relación semántica simétrica entre conceptos ontológicos'),
    ('EXPRESSES', true, 'Símbolo o archivo expresa un concepto o definición de negocio')
ON CONFLICT (tipo) DO NOTHING;

-- 3. Vista de catálogo generado de reglas válidas de aristas (metamodelo)
CREATE OR REPLACE VIEW cm.v_catalogo_grafo AS
SELECT 
    e.tipo AS edge_type,
    e.es_dirigida,
    e.descripcion AS edge_descripcion,
    array_agg(DISTINCT n_src.kind ORDER BY n_src.kind) AS src_kinds_validos,
    array_agg(DISTINCT n_dst.kind ORDER BY n_dst.kind) AS dst_kinds_validos
FROM cm.edge_types e
CROSS JOIN cm.node_types n_src
CROSS JOIN cm.node_types n_dst
GROUP BY e.tipo, e.es_dirigida, e.descripcion;

-- Permisos de lectura
GRANT SELECT ON cm.node_types TO kallicode_app, kallicode_worker;
GRANT SELECT ON cm.edge_types TO kallicode_app, kallicode_worker;
GRANT SELECT ON cm.v_catalogo_grafo TO kallicode_app, kallicode_worker;

