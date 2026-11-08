# Esquema `cm` — CodeMapping (Postgres)

KC-ONB-02-4 · Especificación técnica acordada con el gremio de datos (Equipo 2 — Database)

Modelo relacional del grafo de conocimiento de CodeMapping, según el «Plan Scrum ·
CodeMapping» (decisión D18: retira Neo4j y Qdrant, todo pasa a PostgreSQL 16 + pgvector).
Fuente de verdad del DDL: Database (decisión D06); CodeMapping lo especifica
conjuntamente. Aislamiento multi-tenant estándar del proyecto: `tenant_id` + RLS.

Persistencia (ADR-018): el grafo (`cm.*`) y los vectores (`vec.*`) viven en PostgreSQL; no hay
referencias a Neo4j ni a Qdrant. Los embeddings cuelgan de `cm.nodes` por FK compuesta (ver
«`cm.nodes` y su relación con `vec`»).

A diferencia de una versión anterior de este documento (una tabla puente por tipo de
relación), este usa **una sola tabla `cm.edges` genérica** para todas las aristas del grafo, más
`cm.repo_snapshots` para versionar cada ingesta.

```sql
CREATE SCHEMA IF NOT EXISTS cm;
```

## `cm.repo_snapshots`
Una fila por ejecución de ingesta (completa o refresco). Punto de control por fase para
reanudar sin repetir trabajo; el grafo de un snapshot no se publica hasta `estado = 'completo'`.
```sql
CREATE TABLE cm.repo_snapshots (
    id             text NOT NULL,                 -- snap_<ulid>
    tenant_id      text NOT NULL REFERENCES core.organizations(id),
    commit_sha     text NOT NULL,
    rama           text NOT NULL,
    estado         text NOT NULL DEFAULT 'en_progreso',
    fase_actual    text,                           -- último punto de control confirmado
    stats          jsonb NOT NULL DEFAULT '{}',    -- recuentos por capa, duración por fase
    iniciado_en    timestamptz NOT NULL DEFAULT now(),
    finalizado_en  timestamptz,
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT snapshots_estado_chk CHECK (estado IN ('en_progreso','completo','fallido','pendiente_intervencion'))
);
```

## `cm.files`
Un archivo fuente ingerido, asociado al snapshot que lo cargó.
```sql
CREATE TABLE cm.files (
    id            text NOT NULL,                   -- file_<ulid>
    tenant_id     text NOT NULL REFERENCES core.organizations(id),
    path          text NOT NULL,
    language      text NOT NULL,
    sha256        text NOT NULL,
    snapshot_id   text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, path),
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm.repo_snapshots(tenant_id, id)
);
```

## `cm.symbols`
Función, método, clase o interfaz extraída de un archivo.
```sql
CREATE TABLE cm.symbols (
    id           text NOT NULL,                    -- sha1(path:name:línea:col) o UUIDv5 por tenant
    tenant_id    text NOT NULL REFERENCES core.organizations(id),
    file_id      text NOT NULL,
    kind         text NOT NULL,
    name         text NOT NULL,
    start_line   integer NOT NULL,
    end_line     integer NOT NULL,
    signature    text NOT NULL,
    descripcion  text,                              -- la llena la fase semántica (LLM)
    role         text,                              -- domain | infra | glue
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, file_id) REFERENCES cm.files(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT symbols_kind_chk CHECK (kind IN ('function','method','class','interface','enum','type')),
    CONSTRAINT symbols_role_chk CHECK (role IS NULL OR role IN ('domain','infra','glue'))
);
```

## `cm.db_tables`
Tabla de la base de datos del cliente (determinista, vía `information_schema`).
```sql
CREATE TABLE cm.db_tables (
    id        text NOT NULL,                        -- dbt_<ulid>
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    esquema   text NOT NULL,
    nombre    text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, esquema, nombre)
);
```

## `cm.db_columns`
Columna de una tabla del cliente.
```sql
CREATE TABLE cm.db_columns (
    id        text NOT NULL,                        -- dbc_<ulid>
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    table_id  text NOT NULL,
    nombre    text NOT NULL,
    tipo      text NOT NULL,
    nullable  boolean NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, table_id, nombre),
    FOREIGN KEY (tenant_id, table_id) REFERENCES cm.db_tables(tenant_id, id) ON DELETE CASCADE
);
```

## `cm.definitions`
Agrupación técnica de símbolos que implementan una capacidad (ej. "creación de factura").
```sql
CREATE TABLE cm.definitions (
    id          text NOT NULL,                      -- def_<ulid>
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    modulo      text NOT NULL,
    nombre      text NOT NULL,
    kind        text NOT NULL,
    descripcion text NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
```

## `cm.concepts`
Concepto de negocio del cliente (ej. "Cliente", "Factura"), inducido por LLM y curado por un humano.
```sql
CREATE TABLE cm.concepts (
    id          text NOT NULL,                      -- con_<ulid>
    tenant_id   text NOT NULL REFERENCES core.organizations(id),
    name_en     text NOT NULL,
    name_local  text NOT NULL,
    descripcion text NOT NULL,
    confianza   numeric(4,3),
    curado_por  text REFERENCES core.users(id),
    curado_en   timestamptz,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, name_en),
    CONSTRAINT concepts_conf_chk CHECK (confianza IS NULL OR (confianza >= 0 AND confianza <= 1))
);
```

## `cm.edges`
Toda arista del grafo, de cualquier tipo, en una sola tabla genérica. `tipo` reemplaza los
antiguos tipos de relación de Neo4j (`DEFINES`, `CALLS`, `HAS_COLUMN`, `REFERENCES`,
`USES_TABLE`, `REALIZED_BY`, `RELATES`, `EXPRESSES`); `src_kind`/`dst_kind` identifican la
tabla del nodo (`file`, `symbol`, `db_table`, `db_column`, `definition`, `concept`).
```sql
CREATE TABLE cm.edges (
    tenant_id text NOT NULL REFERENCES core.organizations(id),
    tipo      text NOT NULL,
    src_kind  text NOT NULL,
    src_id    text NOT NULL,
    dst_kind  text NOT NULL,
    dst_id    text NOT NULL,
    props     jsonb NOT NULL DEFAULT '{}',          -- ej. {"confidence": 0.9} en EXPRESSES
    PRIMARY KEY (tenant_id, tipo, src_id, dst_id),
    CONSTRAINT edges_tipo_chk CHECK (tipo IN
        ('DEFINES','CALLS','HAS_COLUMN','REFERENCES','USES_TABLE','REALIZED_BY','RELATES','EXPRESSES')),
    CONSTRAINT edges_kind_chk CHECK (
        src_kind IN ('file','symbol','db_table','db_column','definition','concept') AND
        dst_kind IN ('file','symbol','db_table','db_column','definition','concept'))
);
```

## `cm.curation_queue`
Cola de curación humana: nada generado por el LLM con confianza baja entra al grafo sin pasar por aquí.
```sql
CREATE TABLE cm.curation_queue (
    id           text NOT NULL,                      -- cq_<ulid>
    tenant_id    text NOT NULL REFERENCES core.organizations(id),
    tipo         text NOT NULL,                       -- concepto | enlace | nugget
    payload      jsonb NOT NULL,
    estado       text NOT NULL DEFAULT 'pendiente',
    revisado_por text REFERENCES core.users(id),
    revisado_en  timestamptz,
    creado_en    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT curation_estado_chk CHECK (estado IN ('pendiente','aprobado','rechazado'))
);
```

## `cm.nuggets`
Reglas de contexto cortas que las plantillas del pipeline consumen vía `{{nuggets}}`.
```sql
CREATE TABLE cm.nuggets (
    id            text NOT NULL,                     -- ng_<ulid>
    tenant_id     text NOT NULL REFERENCES core.organizations(id),
    regla         text NOT NULL,
    scope_refs    jsonb NOT NULL DEFAULT '[]',
    evidencia_ref text NOT NULL,
    estado        text NOT NULL DEFAULT 'propuesto',
    PRIMARY KEY (tenant_id, id),
    CONSTRAINT nuggets_estado_chk CHECK (estado IN ('propuesto','aprobado','rechazado'))
);
```

### Curación humana obligatoria (KC-CM-08-2, `041_nuggets_curacion.sql`)
La 041 añade `tipo` (`regla_negocio` | `restriccion_tecnica`), `seccion` (sección de origen),
`cita` (texto literal del chunk), `propuesto_en`, `resuelto_por` (FK compuesta a `core.users`),
`resuelto_en` y `motivo_resolucion`.

- **Evidencia resoluble:** `evidencia_ref` es el id del chunk de documentación que respalda la regla,
  con FK compuesta `(tenant_id, evidencia_ref) → vec.doc_chunks` `ON DELETE CASCADE`. Si el chunk
  desaparece (el documento se reprocesa más corto o falla), el nugget cae con él.
- **Longitud:** `char_length(regla) <= cm.nugget_regla_max_chars()` (280), el mismo valor que
  `kallicode_codemapping.nuggets.REGLA_MAX_CHARS`, para caber en el presupuesto de `{{nuggets}}`.
- **Ciclo de vida:**

| Paso | Quién | Qué garantiza la base |
|---|---|---|
| Propuesta | `nuggets.proponer_nuggets` (build_graph, tras ingerir documentos) | Todo INSERT nace `propuesto` sin resolutor (trigger `nuggets_curacion`) y abre una revisión `pendiente` en `cm.curation_queue` con `payload.nugget_id` (trigger `nuggets_encolar`). |
| Resolución | Persona curadora: `SELECT cm.resolver_nugget(nugget, usuario, 'aprobado'\|'rechazado', motivo, regla_editada)` | Registra la decisión en la revisión (`revisado_por`, `revisado_en`) y la aplica al nugget en una transacción. Un UPDATE directo a `aprobado`/`rechazado` falla si la cola no tiene ESA resolución (mismo estado, persona e instante). |
| Después | — | Un nugget resuelto no se modifica. Si se borra, su revisión pendiente se retira; las resueltas quedan como historial. |

"Pendiente" en el ticket es el estado `propuesto` del nugget más la revisión `pendiente` de la cola.
Desde el portal, `POST /codemapping/curation/{id}/decision` resuelve los elementos `nugget` con
`cm.resolver_nugget()` (KC-CM-08-3).

### Resolución de `{{nuggets}}` (KC-CM-08-3, contrato `cm-consulta-v1`)
Las plantillas del pipeline reciben las reglas **aprobadas** del tenant de la sesión que aplican al
área que se va a tocar. Contrato congelado: `contratos/cm-consulta-v1.schema.json`; fuente única:
`kallicode_core.cm_consulta` (Backend, async) y `kallicode_codemapping.cm_queries` (pipeline, psycopg).

| Variable | Área | Tipos | Generales |
|---|---|---|---|
| `{{nuggets}}` | nodos y ficheros pedidos (con sus símbolos) | todos | sí, al final |
| `{{module_conventions}}` | ficheros bajo el prefijo de carpeta del módulo | `restriccion_tecnica` | no |
| `{{nuggets_for_file}}` | un fichero y sus símbolos | todos | no |

Relevancia: **directa** (el chunk de evidencia está enlazado en `cm.doc_chunk_links` a un nodo del
área) > **alcance** (un `scope_ref` coincide, sin distinguir mayúsculas, con la ruta, el símbolo o la
tabla del área) > **general** (sin ningún vínculo con el grafo). Un nugget vinculado a otra área no
se entrega. Orden: relevancia, `score` descendente, `resuelto_en`, `id` (collation "C"). El recorte
entra reglas enteras ("- <regla>" por línea, 20 como máximo, 5660 caracteres por defecto) y se
detiene en la primera que no cabe. Sin reglas, la variable es `""`.

## `cm.incidents`
Memoria de fallos del cliente: un ticket cerrado con incidente deja su causa, corrección y nodos afectados.
```sql
CREATE TABLE cm.incidents (
    id                text NOT NULL,                 -- inc_<ulid>
    tenant_id         text NOT NULL REFERENCES core.organizations(id),
    ticket_id         text REFERENCES core.tickets(id),
    resumen           text NOT NULL,
    causa             text,
    correccion        text,
    nodos_afectados   jsonb NOT NULL DEFAULT '[]',
    creado_en         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
```

## Refresco por snapshot y cola de curación (KC-DB-04-3, migración 019)
Cierra CM-F05: el refresco con MERGE incremental dejaba nodos y aristas fantasma. Ahora:

1. El snapshot nuevo se carga en `en_progreso`; cada nodo/arista que sube lleva su `snapshot_id`
   (`files`, `db_tables`, `definitions`, `concepts`, `edges`; `symbols` y `db_columns` cuelgan de
   `files` y `db_tables` por cascada).
2. `cm.publicar_snapshot(snapshot, nodos, aristas)` verifica los recuentos contra
   `cm.stats_snapshot()`, marca `completo` el nuevo, mueve `cm.snapshot_vigente` y marca
   `retirado` el anterior, todo en la transacción del llamador (atómico; si algo falla no cambia nada).
3. `cm.retirar_snapshot(snapshot)` (diferida) borra los nodos del snapshot `retirado` y sus aristas,
   incluidas las que quedan colgando de un nodo borrado. Lo que seguía marcado con el snapshot viejo
   es lo que el nuevo no vio: los fantasmas. Lo ingerido antes de la 019 (`snapshot_id` NULL) no se toca.
4. Triggers `edges_confianza` y `concepts_confianza`: una arista (`props.confidence`) o concepto
   (`confianza`) por debajo de `cm.umbral_confianza()` (0,75) no se inserta y queda `pendiente` en
   `cm.curation_queue` (sin duplicar). Exactamente 0,75 entra.

Pendiente en CodeMapping: el loader debe marcar `snapshot_id` en cada upsert y llamar a
`publicar_snapshot` / `retirar_snapshot` al cerrar la corrida.

## `cm.nodes` y su relación con `vec` (KC-DB-04-4, migración 022)
Los nodos viven en seis tablas (`files`, `symbols`, `db_tables`, `db_columns`, `definitions`,
`concepts`). `cm.nodes (tenant_id, id, kind)` es su registro único: triggers `nodes_sync_ins` y
`nodes_sync_del` en cada tabla la mantienen al día, y `kind` es uno de `cm.node_types`. Un `id`
no puede repetirse entre kinds dentro de un tenant.

`vec.definition_embeddings` se consolida contra ella (hallazgo DB-F18; persistencia ADR-018):

- `nodo_ref` es FK compuesta `de_nodo_fk (tenant_id, nodo_ref) -> cm.nodes (tenant_id, id)` con
  `ON DELETE CASCADE`: borrar un nodo borra su embedding en la misma transacción, y un embedding
  no puede apuntar a un nodo de otro tenant.
- `capa` es el kind del nodo; `de_capa_chk` admite exactamente `file | symbol | db_table |
  db_column | definition | concept` y rechaza los tres valores antiguos (`definicion`, `negocio`,
  `codigo`).
- La migración convirtió los datos existentes y dejó el resumen en `vec.acta_conversion_capa`
  (convertidos por par capa antigua -> kind, y los huérfanos eliminados con sus ids).

## RLS y permisos
`db/04_rls.sql` corre antes que `db/migrations/` en `make db` (ver `Makefile`), así que no se
puede agregar `cm` a su lista de schemas — el `GRANT USAGE ON SCHEMA cm` fallaría en una
instalación nueva por no existir todavía el esquema en ese punto. El RLS y los permisos de `cm`
van autocontenidos al final de `db/migrations/002_esquema_cm.sql`, con el mismo patrón
(`ENABLE ROW LEVEL SECURITY` + política `tenant_id = core.current_tenant()` por tabla).
