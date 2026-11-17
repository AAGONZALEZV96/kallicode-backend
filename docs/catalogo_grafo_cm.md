# Catálogo de nodos y aristas del grafo de CodeMapping

> **Documento generado. No lo edites a mano.** Sale del esquema `cm` ya migrado con
> `python db/tools/catalogo_grafo_cm.py` (KC-CM-12-3) y el CI lo compara con esta copia en cada PR:
> si añades un tipo de nodo o de arista en una migración, regenera el documento en el mismo PR.

Fuentes: `cm.node_types`, `cm.edge_types`, los CHECK `edges_tipo_chk` y `edges_kind_chk` de
`cm.edges`, `symbols_kind_chk` de `cm.symbols`, los triggers `nodes_sync_ins` / `nodes_sync_del`
que mantienen `cm.nodes` y los COMMENT del esquema. La generación falla si esas fuentes se
contradicen.

## 1. Resumen

- **6 tipos de nodo** en 3 categorías.
- **8 tipos de arista**, 7 dirigidos.
- Todo nodo tiene una fila en `cm.nodes (tenant_id, id, kind)`; el id es único por tenant sea cual
  sea su kind. Toda arista es una fila de `cm.edges (tenant_id, tipo, src_id, dst_id)`.
- Aislamiento: todas las tablas llevan `tenant_id` y RLS por tenant.

## 2. Tipos de nodo

| Kind | Categoría | Tabla que lo respalda | Descripción |
|---|---|---|---|
| `db_column` | base_datos | `cm.db_columns` | Columna relacional con atributos y tipos |
| `db_table` | base_datos | `cm.db_tables` | Tabla relacional de esquema SQL analizado |
| `file` | codigo | `cm.files` | Archivo fuente indexado en el repositorio |
| `symbol` | codigo | `cm.symbols` | Símbolo del AST: función, clase, método, interfaz, enum o tipo |
| `concept` | semantica | `cm.concepts` | Concepto de dominio o término de ontología |
| `definition` | semantica | `cm.definitions` | Unidad de definición semántica vectorizada |

Subtipos de `symbol` (columna `cm.symbols.kind`, CHECK `symbols_kind_chk`): `function`, `method`, `class`, `interface`, `enum`, `type`, `view`, `procedure`.

## 3. Tipos de arista

| Tipo | Dirigida | Descripción |
|---|---|---|
| `CALLS` | sí | Invocación o referencia de llamada entre símbolos |
| `DEFINES` | sí | El archivo u objeto define un símbolo o entidad |
| `EXPRESSES` | sí | Símbolo o archivo expresa un concepto o definición de negocio |
| `HAS_COLUMN` | sí | Pertenencia estructural de una columna a una tabla |
| `REALIZED_BY` | sí | Concepto de negocio materializado en código o tabla |
| `REFERENCES` | sí | Referencia foránea o dependencia entre tablas/entidades |
| `RELATES` | no | Relación semántica simétrica entre conceptos ontológicos |
| `USES_TABLE` | sí | Símbolo de código ejecuta lecturas o escrituras sobre tabla SQL |

Extremos admitidos por `edges_kind_chk`:

- `src_kind`: `file`, `symbol`, `db_table`, `db_column`, `definition`, `concept`.
- `dst_kind`: `file`, `symbol`, `db_table`, `db_column`, `definition`, `concept`.

> El esquema no restringe qué pares de kinds admite cada tipo de arista: cualquier tipo puede unir
> cualquier par de los de arriba (la vista `cm.v_catalogo_grafo` también los da todos por válidos).
> Qué pares produce de verdad cada fase del pipeline está en
> [packages/codemapping/README.md](../packages/codemapping/README.md).

### Columnas de `cm.edges`

Aristas del grafo de CodeMapping entre nodos de cualquier tipo; las deterministas se reconstruyen desde el repositorio y la base del cliente, las semánticas se restauran de la copia.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `tipo` | `text` | sí | Tipo de arista (cm.edge_types.tipo). |
| `src_kind` | `text` | sí | Tipo del nodo de origen (cm.node_types.kind). |
| `src_id` | `text` | sí | Nodo de origen (cm.nodes.id). |
| `dst_kind` | `text` | sí | Tipo del nodo de destino (cm.node_types.kind). |
| `dst_id` | `text` | sí | Nodo de destino (cm.nodes.id). |
| `props` | `jsonb` | sí | Propiedades adicionales de la arista, en JSON. |
| `snapshot_id` | `text` | no | Snapshot del repositorio (cm.repo_snapshots) en el que se produjo la fila. |

## 4. Relaciones con nodos fuera de `cm.edges`

Tablas que apuntan a un nodo por clave foránea a `cm.nodes` (la acción al borrar el nodo
está en la definición).

| Tabla | Constraint | Definición | Qué guarda |
|---|---|---|---|
| `cm.doc_chunk_links` | `dcl_nodo_fk` | `FOREIGN KEY (tenant_id, nodo_ref) REFERENCES cm.nodes(tenant_id, id) ON DELETE CASCADE` | Enlaces entre fragmentos de documentación (vec.doc_chunks) y nodos del grafo, con el método y la puntuación con que se estableció el vínculo. |
| `cm.incident_nodes` | `incn_nodo_fk` | `FOREIGN KEY (tenant_id, nodo_ref) REFERENCES cm.nodes(tenant_id, id) ON DELETE CASCADE` | Relación entre un incidente de producción y los nodos del grafo que afectó. |
| `vec.definition_embeddings` | `de_nodo_fk` | `FOREIGN KEY (tenant_id, nodo_ref) REFERENCES cm.nodes(tenant_id, id) ON DELETE CASCADE` | Embeddings de los nodos del grafo (símbolos, definiciones y conceptos) para búsqueda semántica; referencia a cm.nodes y se borra con el nodo. |

## 5. Tablas de nodo

### `cm.concepts` — kind `concept`

Conceptos de dominio del grafo de CodeMapping (ontología del cliente) con su nombre en inglés y en el idioma local; capa semántica propuesta por el LLM y curable por personas.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `name_en` | `text` | sí | Nombre del concepto en inglés (forma canónica). |
| `name_local` | `text` | sí | Nombre del concepto en el idioma del tenant. |
| `descripcion` | `text` | sí | Texto descriptivo. |
| `confianza` | `numeric(4,3)` | no | Confianza del LLM en el concepto, de 0 a 1. |
| `curado_por` | `text` | no | Persona que curó el concepto; nulo si aún no lo revisó nadie. |
| `curado_en` | `timestamp with time zone` | no | Instante de la curación. |
| `snapshot_id` | `text` | no | Snapshot del repositorio (cm.repo_snapshots) en el que se produjo la fila. |

### `cm.db_columns` — kind `db_column`

Columnas de las tablas de la base de datos del cliente, extraídas del catálogo del cliente en la fase de esquema del grafo.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `table_id` | `text` | sí | Tabla del cliente (cm.db_tables.id) a la que pertenece la columna. |
| `nombre` | `text` | sí | Nombre de la columna en la base del cliente. |
| `tipo` | `text` | sí | Tipo de dato de la columna en la base del cliente. |
| `nullable` | `boolean` | sí | Verdadero si la columna del cliente admite NULL. |

### `cm.db_tables` — kind `db_table`

Tablas de la base de datos del cliente que forman parte del grafo (nodos de tipo db_table).

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `esquema` | `text` | sí | Esquema de la tabla en la base del cliente. |
| `nombre` | `text` | sí | Nombre de la tabla en la base del cliente. |
| `snapshot_id` | `text` | no | Snapshot del repositorio (cm.repo_snapshots) en el que se produjo la fila. |

### `cm.definitions` — kind `definition`

Definiciones de negocio o de módulo del grafo: descripciones consolidadas de qué hace un módulo o una regla; capa semántica generada por el LLM.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `modulo` | `text` | sí | Módulo del repositorio al que pertenece la definición. |
| `nombre` | `text` | sí | Nombre de la definición. |
| `kind` | `text` | sí | Clase de definición dentro de la capa semántica. |
| `descripcion` | `text` | sí | Descripción consolidada, generada por el LLM. |
| `snapshot_id` | `text` | no | Snapshot del repositorio (cm.repo_snapshots) en el que se produjo la fila. |

### `cm.files` — kind `file`

Archivos del repositorio del cliente ingeridos en un snapshot (nodos de tipo file), con su lenguaje y hash de contenido.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `path` | `text` | sí | Ruta del archivo relativa a la raíz del repositorio. |
| `language` | `text` | sí | Lenguaje detectado por la extensión. |
| `sha256` | `text` | sí | Hash SHA-256 del contenido; permite detectar cambios entre snapshots. |
| `snapshot_id` | `text` | sí | Snapshot del repositorio (cm.repo_snapshots) en el que se produjo la fila. |

### `cm.symbols` — kind `symbol`

Símbolos de código extraídos por el analizador (funciones, métodos, clases, interfaces, enums, tipos, vistas y procedimientos) con su ubicación, firma y descripción.

| Columna | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `id` | `text` | sí | Identificador de la fila (clave primaria). |
| `tenant_id` | `text` | sí | Organización (tenant) propietaria de la fila; referencia a core.organizations y base del aislamiento por RLS. |
| `file_id` | `text` | sí | Archivo (cm.files.id) donde se define el símbolo. |
| `kind` | `text` | sí | Tipo del objeto dentro de su catálogo. |
| `name` | `text` | sí | Nombre del símbolo tal como aparece en el código. |
| `start_line` | `integer` | sí | Línea donde empieza el símbolo. |
| `end_line` | `integer` | sí | Línea donde termina el símbolo. |
| `signature` | `text` | sí | Firma del símbolo (cabecera sin cuerpo). |
| `descripcion` | `text` | no | Texto descriptivo. |
| `role` | `text` | no | Rol del símbolo en el sistema (dominio, infraestructura o pegamento), asignado por el LLM; nulo si no se asignó. |
