# CodeMapping (`kallicode_codemapping`)

CodeMapping lee el repositorio y la base de datos de un cliente y construye un **grafo de conocimiento** del
código en PostgreSQL: archivos, símbolos, tablas y columnas, las relaciones entre ellos y una capa semántica
(definiciones de negocio y conceptos). Los agentes del pipeline de Kallicode consultan ese grafo antes de actuar,
para saber qué toca un cambio y qué depende de él.

Todo vive en la base de la plataforma, en los esquemas `cm` (grafo) y `vec` (embeddings), con RLS por tenant.
No hay Neo4j ni Qdrant ([ADR-018](../../docs/adr/ADR-018-grafo-en-postgresql.md)).

| Quiero… | Documento |
|---|---|
| Lanzar una ingesta (local o producción) | [Operación de la ingesta](../../docs/operacion/ingesta-codemapping.md) |
| Consultar el grafo desde Backend o Testing | [API de consulta](../../docs/operacion/api-consulta-codemapping.md) |
| Saber qué tipos de nodo y arista existen | [Catálogo generado del esquema `cm`](../../docs/catalogo_grafo_cm.md) |
| Explicar la curación al cliente | [Guía de curación](../../docs/operacion/guia-curacion-cliente.md) |
| Recuperar el grafo de un tenant | [Runbook de reconstrucción](../../docs/runbook_reconstruccion_grafo.md) |
| Entender por qué PostgreSQL | [ADR-018](../../docs/adr/ADR-018-grafo-en-postgresql.md) y [ADR-020](../../docs/adr/ADR-020-grafo-en-postgresql.md) |

## El pipeline

`build_graph.full_build` orquesta siete fases. Cada corrida es una fila de `cm.repo_snapshots`; el grafo de un
snapshot solo se publica (`cm.snapshot_vigente`) cuando termina en estado `completo`.

| # | Fase | Módulos | Qué produce | Servicios externos |
|---|---|---|---|---|
| 1 | Sincronizar el repositorio | `ingest_repo` | Clon o `fetch` de la rama `main` en `CM_WORKDIR/<tenant>` | Git |
| 2 | Análisis sintáctico | `parser_treesitter`, `resolucion_llamadas` | Nodos `file` y `symbol`; aristas `CALLS` | — |
| 3 | Esquema y uso de datos | `extract_sql`, `detectores_orm` | Nodos `db_table` y `db_column`; aristas `REFERENCES` y `USES_TABLE` | Base del cliente (solo lectura, opcional) |
| 4 | Enriquecimiento semántico | `llm_enrich`, `security`, `budget` | Descripción y rol de cada símbolo; nodos `definition` | LLM |
| 5 | Ontología y enlaces | `ontology_mapreduce`, `curation`, `tenant_thresholds` | Nodos `concept`; aristas `REALIZED_BY`, `EXPRESSES`, `RELATES`; lo dudoso a `cm.curation_queue` | LLM |
| 6 | Embeddings | `embeddings` | Vectores BGE-M3 (1024 dims) en `vec.*` | TEI |
| 7 | Persistencia y publicación | `graph_loader`, `snapshots` | Carga por `COPY`, cierre transitivo `cm.reachability`, snapshot publicado | — |

Después de la fase 7 corren la ingesta documental (`ingest_docs`, `fase_documental`, `nuggets`): documentos del
onboarding troceados, enlazados a nodos (`cm.doc_chunk_links`) y nuggets propuestos para curación.

Las fases 1–3 y la salida del LLM (4–5) se guardan como *checkpoint* en `CM_WORKDIR`: si la corrida muere,
relanzar el mismo comando sobre el mismo commit reanuda el snapshot sin volver a pagar el LLM (`checkpoints`,
`snapshots`).

**Alcances.** `--alcance completo` (por defecto) hace las siete fases. `--alcance determinista` hace solo 1–3 y 7:
no llama al LLM ni a TEI. Se usa para recuperación ante desastre y para probar la instalación.

**Refresco.** `build_graph --refresh` (`refresh`) procesa solo los archivos cambiados desde el último snapshot y, al
final, reembebe los vectores obsoletos (`vigencia`).

### Qué aristas escribe de verdad cada fase

El esquema admite ocho tipos de arista entre cualquier par de kinds ([catálogo](../../docs/catalogo_grafo_cm.md)).
El cargador (`graph_loader`) escribe estos pares:

| Tipo | Origen → destino | Escrita por |
|---|---|---|
| `CALLS` | `symbol` → `symbol` | `load_symbols` (llamadas resueltas por nombre e imports) |
| `REFERENCES` | `db_table` → `db_table` | `load_schema` (claves foráneas de la base del cliente) |
| `USES_TABLE` | `file` → `db_table` | `load_schema` (SQL crudo y ORM detectados en el archivo) |
| `REALIZED_BY` | `definition` → `symbol` | capa semántica |
| `EXPRESSES` | `definition` → `concept` | capa semántica (con `props.confidence`) |
| `RELATES` | `concept` → `concept` | capa semántica (no dirigida) |
| `DEFINES` | — | **No se escribe.** La pertenencia archivo → símbolo es la FK `cm.symbols.file_id` |
| `HAS_COLUMN` | — | **No se escribe.** La pertenencia tabla → columna es la FK `cm.db_columns.table_id` |

Los consumidores no dependen de `DEFINES`: las semillas de tipo archivo se expanden a sus símbolos
(`kallicode_core.cm_impacto`). La desviación está registrada en el ADR-018.

## Otros módulos

| Módulo | Para qué |
|---|---|
| `worker` | Consumidor de Redis Streams que ejecuta la ingesta con idempotencia, reintentos, DLQ y escalación ([worker_ingesta.md](../../docs/worker_ingesta.md)) |
| `escalacion` | Pausa un snapshot bloqueado y pide intervención humana (KC-CM-10-6) |
| `metricas` | `/metrics` de Prometheus (puerto 9108 por defecto) |
| `consulta_grafo` | Cliente del contrato `cm-grafo-v1` sobre PostgreSQL |
| `cm_queries` | Resolución de `{{nuggets}}` (`cm-consulta-v1`) y búsqueda híbrida |
| `recuperacion` | Recuperación en dos fases para Diseño y detección de tickets duplicados |
| `incidentes`, `realimentacion` | Memoria de incidentes y realimentación del grafo desde tickets entregados |
| `secretos`, `config` | Credenciales desde la bóveda por tenant y configuración `CM_*` |
| `evaluacion_calidad`, `evaluacion_busqueda` | Evaluación de calidad semántica y de búsqueda (CI nocturno/semanal) |
| `repo_sintetico` | Repositorio sintético para benchmarks; no es código de producción |

## Configuración

Todo se configura con variables `CM_*` (`config.Settings`, pydantic-settings). Las imprescindibles:

| Variable | Qué es |
|---|---|
| `CM_TENANT_ID` | Tenant (`org_…`). Obligatoria y sin valor por defecto |
| `CM_REPO_URL` | URL o ruta del repositorio. Debe tener rama `main` |
| `CM_STORE_DATABASE_URL` | PostgreSQL de Kallicode **con el rol `kallicode_app`** (`kallicode_worker` no tiene permisos en `cm`) |
| `CM_DATABASE_URL` | Base del cliente para la fase 3. Opcional: sin ella no hay tablas ni `USES_TABLE` |
| `CM_TEI_ENDPOINT_URL` | TEI con BGE-M3 (fase 6) |
| `KC_LLM_FLASH_*`, `KC_LLM_PRO_*` | Proveedor LLM compatible con OpenAI (fases 4–5), por tier. Lo lee el cliente de `kallicode_core`: `CM_LLM_*` y `CM_MODEL_*` no tienen efecto |
| `CM_WORKDIR` | Directorio de clones y checkpoints (por defecto `./workdir`) |

La lista completa, con valores por defecto, está en `config.py` y en la
[guía de operación](../../docs/operacion/ingesta-codemapping.md#3-configuración).

## Desarrollo

```bash
uv sync --all-packages
uv run ruff check packages/codemapping && uv run ruff format --check packages/codemapping
uv run mypy packages/codemapping
uv run pytest packages/codemapping          # las pruebas con base se omiten si no hay PostgreSQL
```

- `test/`: pruebas unitarias, sin servicios. `tests/`: integración con PostgreSQL y Redis reales, benchmarks
  (`bench_*`) y ensayos (`ensayo_*`).
- Cobertura mínima del paquete en CI: 70 %.
- Pruebas de mutación: `docs/calidad/mutacion_codemapping.md`.
- El fixture `tests/fixtures/repo_ref` es el repositorio de referencia del analizador y de la evaluación de
  calidad; no se edita sin regenerar los *golden*.

## Seguridad

- El código y los documentos del cliente son **entrada no confiable**: se sanean y van delimitados con un nonce
  en los prompts, y la salida del LLM se valida contra JSON Schema (`security`, `llm_enrich`).
- Credenciales solo por referencia (bóveda o variables de entorno); se enmascaran en logs, DLQ y escalaciones.
- La base del cliente se lee en una transacción de solo lectura, con `statement_timeout` y tope de conexiones.
