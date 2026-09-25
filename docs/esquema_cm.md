# Esquema `cm` — CodeMapping (Postgres)

KC-ONB-02-4 · Especificación técnica acordada con el gremio de datos

Modela el grafo de código de CodeMapping (archivos, símbolos, tablas del
cliente, definiciones técnicas y conceptos de negocio) como tablas
relacionales en Postgres, bajo un esquema nuevo `cm` (junto a `core`,
`vec`, `audit`). Aislamiento multi-tenant estándar del proyecto:
`tenant_id` + RLS.

```sql
CREATE SCHEMA IF NOT EXISTS cm;
```

## `cm.files`
Un archivo fuente del repositorio del cliente.
```sql
CREATE TABLE cm.files (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    path        text NOT NULL,               -- relativo a la raíz del repo
    language    text NOT NULL,
    sha256      text NOT NULL,
    creado_en   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, path)
);
```

## `cm.symbols`
Función, método, clase o interfaz extraída de un archivo.
```sql
CREATE TABLE cm.symbols (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    id          text NOT NULL,               -- sha1(path:name:linea:col)
    file_path   text NOT NULL,
    name        text NOT NULL,
    kind        text NOT NULL,               -- function|method|class|interface|enum|type
    start_line  integer NOT NULL,
    end_line    integer NOT NULL,
    signature   text NOT NULL,
    creado_en   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, file_path) REFERENCES cm.files(tenant_id, path) ON DELETE CASCADE,
    CONSTRAINT symbols_kind_chk CHECK (kind IN ('function','method','class','interface','enum','type'))
);
```

## `cm.symbol_calls`
Qué símbolo llama a qué símbolo.
```sql
CREATE TABLE cm.symbol_calls (
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    from_symbol_id text NOT NULL,
    to_symbol_id   text NOT NULL,
    PRIMARY KEY (tenant_id, from_symbol_id, to_symbol_id),
    FOREIGN KEY (tenant_id, from_symbol_id) REFERENCES cm.symbols(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, to_symbol_id)   REFERENCES cm.symbols(tenant_id, id) ON DELETE CASCADE
);
```

## `cm.tables` / `cm.columns`
Estructura de la base de datos del cliente (vía `information_schema`).
```sql
CREATE TABLE cm.tables (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    name        text NOT NULL,
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE cm.columns (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    table_name  text NOT NULL,
    name        text NOT NULL,
    type        text NOT NULL,
    PRIMARY KEY (tenant_id, table_name, name),
    FOREIGN KEY (tenant_id, table_name) REFERENCES cm.tables(tenant_id, name) ON DELETE CASCADE
);
```

## `cm.table_references`
Llaves foráneas entre tablas del cliente.
```sql
CREATE TABLE cm.table_references (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    from_table  text NOT NULL,
    to_table    text NOT NULL,
    PRIMARY KEY (tenant_id, from_table, to_table),
    FOREIGN KEY (tenant_id, from_table) REFERENCES cm.tables(tenant_id, name) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, to_table)   REFERENCES cm.tables(tenant_id, name) ON DELETE CASCADE
);
```

## `cm.file_table_usage`
Qué archivo usa qué tabla del cliente.
```sql
CREATE TABLE cm.file_table_usage (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    file_path   text NOT NULL,
    table_name  text NOT NULL,
    PRIMARY KEY (tenant_id, file_path, table_name),
    FOREIGN KEY (tenant_id, file_path)  REFERENCES cm.files(tenant_id, path)  ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, table_name) REFERENCES cm.tables(tenant_id, name) ON DELETE CASCADE
);
```

## `cm.definitions`
Agrupación técnica de símbolos que implementan una capacidad (ej. "creación de factura").
```sql
CREATE TABLE cm.definitions (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    id          text NOT NULL,               -- def_<ulid>
    name        text NOT NULL,
    kind        text NOT NULL,
    description text NOT NULL,
    creado_en   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
```

## `cm.definition_symbols`
Qué símbolos realizan cada definición.
```sql
CREATE TABLE cm.definition_symbols (
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    definition_id  text NOT NULL,
    symbol_id      text NOT NULL,
    PRIMARY KEY (tenant_id, definition_id, symbol_id),
    FOREIGN KEY (tenant_id, definition_id) REFERENCES cm.definitions(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, symbol_id)     REFERENCES cm.symbols(tenant_id, id)     ON DELETE CASCADE
);
```

## `cm.concepts`
Concepto de negocio del cliente (ej. "Cliente", "Factura"), inducido por LLM.
```sql
CREATE TABLE cm.concepts (
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    name_en     text NOT NULL,
    name_local  text NOT NULL,
    description text NOT NULL,
    confidence  numeric(4,3),
    PRIMARY KEY (tenant_id, name_en),
    CONSTRAINT concepts_conf_chk CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);
```

## `cm.concept_relations`
Relaciones entre conceptos de negocio (ej. "Factura" *pertenece a* "Cliente").
```sql
CREATE TABLE cm.concept_relations (
    tenant_id     text NOT NULL REFERENCES core.organizations(id),
    from_concept  text NOT NULL,
    to_concept    text NOT NULL,
    kind          text NOT NULL,             -- has | belongs_to | produces | settles...
    PRIMARY KEY (tenant_id, from_concept, to_concept, kind),
    FOREIGN KEY (tenant_id, from_concept) REFERENCES cm.concepts(tenant_id, name_en) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, to_concept)   REFERENCES cm.concepts(tenant_id, name_en) ON DELETE CASCADE
);
```

## `cm.definition_concepts`
Qué conceptos de negocio expresa cada definición técnica, con confianza (0..1).
```sql
CREATE TABLE cm.definition_concepts (
    tenant_id        text NOT NULL REFERENCES core.organizations(id),
    definition_id    text NOT NULL,
    concept_name_en  text NOT NULL,
    confidence       numeric(4,3) NOT NULL,
    PRIMARY KEY (tenant_id, definition_id, concept_name_en),
    FOREIGN KEY (tenant_id, definition_id)   REFERENCES cm.definitions(tenant_id, id)   ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, concept_name_en) REFERENCES cm.concepts(tenant_id, name_en) ON DELETE CASCADE,
    CONSTRAINT defconcepts_conf_chk CHECK (confidence >= 0 AND confidence <= 1)
);
```

## `cm.refresh_state`
Último commit (SHA) procesado por tenant, para el refresh incremental.
```sql
CREATE TABLE cm.refresh_state (
    tenant_id      text PRIMARY KEY REFERENCES core.organizations(id),
    last_sha       text NOT NULL,
    actualizado_en timestamptz NOT NULL DEFAULT now()
);
```

## Relación con `vec`
`vec.definition_embeddings.nodo_ref` referencia `cm.definitions.id` o
`cm.concepts.name_en`, según `capa` (`definicion` | `negocio` | `codigo`).

## Nota para la implementación (no aplica en este ticket)
`db/04_rls.sql` activa RLS y permisos recorriendo una lista fija de
schemas (`core`, `vec`, `audit`, líneas 14–16 y 38). Cuando se migre este
esquema, agregar `cm` a esa lista — no queda cubierto automáticamente solo
por tener `tenant_id`.
