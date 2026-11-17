# ADR-018: El grafo y los vectores de CodeMapping viven en PostgreSQL (se retiran Neo4j y Qdrant)

- **Estado:** aceptado y **cerrado** con el resultado real de la migración (KC-CM-12-3, 2026-11-17). Pendiente de la
  firma del Tech Lead (tabla final).
- **Decisión en una línea:** el grafo de código (`cm.*`) y sus embeddings (`vec.*`) se guardan en el PostgreSQL 16 +
  pgvector de la plataforma, con RLS por tenant. Neo4j y Qdrant se retiran.
- **Relacionados:** [ADR-020](ADR-020-grafo-en-postgresql.md) (cómo se consulta el grafo: las ocho consultas
  críticas y sus requisitos), [ADR-022](ADR-022-representacion-vectorial.md) (representación de los vectores),
  [ADR-003](003-pgvector-multi-tenant.md) (pgvector multi-tenant).

> **Por qué «reconstruido».** El código cita el ADR-018 desde el sprint 3 (`build_graph.py`, `config.py`,
> `docs/esquema_cm.md` y la prueba de cierre de la auditoría), pero el documento nunca llegó al repositorio. Este texto
> reconstruye la decisión a partir de la decisión D18 del plan de CodeMapping y de lo que está implementado. Si aparece
> el original, prevalece y las diferencias se anotan aquí.

## Contexto

El diseño inicial de CodeMapping usaba tres almacenes: Neo4j para el grafo, Qdrant para los vectores y PostgreSQL para
el resto de la plataforma. Eso suponía:

- tres modelos de aislamiento por tenant distintos, cuando el resto de la plataforma usa `tenant_id` + RLS;
- tres copias de seguridad, tres rutas de despliegue y tres superficies de credenciales;
- no poder cruzar el grafo con la curación, los incidentes o los tickets en una misma transacción.

La auditoría de la migración recomendó además **no exportar** el grafo de Neo4j, sino **reconstruirlo desde cero**
con una ingesta completa y validar por recuentos por capa.

## Decisión (prevista)

1. **Grafo** en el esquema `cm`: una tabla por tipo de nodo (`files`, `symbols`, `db_tables`, `db_columns`,
   `definitions`, `concepts`), un registro único `cm.nodes` y una tabla genérica `cm.edges` para todas las aristas.
   El DDL lo mantiene Database (decisión D06) y es la fuente de verdad.
2. **Vectores** en el esquema `vec` con pgvector (BGE-M3, 1024 dimensiones, HNSW), colgando de `cm.nodes` por clave
   foránea compuesta, de modo que borrar un nodo borra su vector.
3. **Aislamiento**: `tenant_id` en todas las tablas y RLS, como el resto de la plataforma.
4. **Versionado**: cada ingesta es un snapshot (`cm.repo_snapshots`) con checkpoints por fase.
5. **Migración**: sin export de Neo4j; reconstrucción con ingesta completa y validación por recuentos.
6. **Retirada**: se eliminan del código, de la configuración y de las dependencias todas las referencias a Neo4j y
   Qdrant.

## Resultado real de la migración

| Punto de la decisión | Resultado | Evidencia |
|---|---|---|
| Grafo en `cm` | Hecho. 6 tipos de nodo y 8 de arista; `cm.nodes` sincronizado por triggers | Migraciones 002, 021, 022; [catálogo generado](../catalogo_grafo_cm.md), comprobado en CI |
| Vectores en `vec` | Hecho. `vec.definition_embeddings` con FK compuesta a `cm.nodes` y `ON DELETE CASCADE`; HNSW con `iterative_scan` | Migración 022; [ADR-022](ADR-022-representacion-vectorial.md); [runbook-vectorial.md](../runbook-vectorial.md) |
| Aislamiento RLS | Hecho. Toda tabla `cm.*` con `tenant_id` y política por `core.current_tenant()` | `packages/codemapping/tests/test_rls_cm.py`; ADR-020, requisito 2 |
| Snapshots y checkpoints | Hecho. Reanudación sin repetir el LLM | `kallicode_codemapping.checkpoints` y `.snapshots`; KC-CM-11-5 |
| Migración por reconstrucción | Hecho como procedimiento probado, no como migración de datos | [Runbook](../runbook_reconstruccion_grafo.md) y [acta](../ensayo_reconstruccion_grafo_acta.md): 200.000 nodos reconstruidos en 49 s con recuentos idénticos |
| Retirada de Neo4j y Qdrant | Hecho en código, configuración y dependencias. Quedan solo menciones históricas en comentarios y en `CONTRIBUTING.md` | `packages/codemapping/tests/test_codemapping_config.py::test_neo4j_y_qdrant_eliminados_del_modelo`; `packages/codemapping/test/test_audit_cierre_13_hallazgos.py` |
| Escala | Ingesta de 200.000 nodos / 260.000 aristas en 0,4 min y 485 MiB; impacto a k = 3 con p95 de 16 ms a 200.000 nodos | [rendimiento_ingesta.md](../rendimiento_ingesta.md), [benchmark-grafo.md](../benchmark-grafo.md) |
| Calidad del grafo | Precisión 0,862 y exhaustividad 0,855 sobre 50 preguntas de impacto | [informe-calidad-cm.md](../calidad/informe-calidad-cm.md) |

## Desviaciones

Diferencias entre lo previsto y lo que hay, encontradas al cerrar este ADR (ensayo de la guía de operación del
2026-11-17 y revisión del cargador). Cada una tiene un responsable propuesto.

| # | Desviación | Impacto | Propuesta |
|---|---|---|---|
| D1 | **El ADR-018 no estaba en el repositorio**: se reconstruye aquí | Decisión sin trazabilidad hasta hoy | Cerrada con este documento |
| D2 | **La ingesta `completo` nunca se había ejecutado de verdad.** Cinco defectos en el mismo camino, ocultos porque las pruebas sustituyen cada paso por un doble y porque mypy tiene desactivados `call-arg` y `arg-type`: (a) `budget.verificar_presupuesto_tenant` consulta `core.ciclo_inicio`, `subscriptions.limite_tokens_mensual` y `usage_tokens.total_tokens`, que no existen, y lo hace sin `app.tenant_id` (con RLS sumaría 0); (b) `build_graph` y `refresh` llaman a `describe_symbols`/`consolidate_definitions` con la firma anterior a KC-CM-05-3; (c) `load_curation_queue` inserta sin transacción y RLS rechaza la fila; (d) la fase 6 manda todos los textos a TEI en una petición (413 por encima de 32); (e) el enriquecimiento no registra su consumo en `core.usage_tokens`. Además, `CM_LLM_*` y `CM_MODEL_*` no tienen efecto | **Bloqueante para GA**: ninguna ingesta con LLM llegaba a la fase 4 | Corregido en `fix/e1-presupuesto-ingesta-cm`: cuota `CM_PRESUPUESTO_TOKENS_TENANT_MES` frente al consumo del ciclo de aniversario, consumo de las fases 4–5 con origen `codemapping_codigo`, prueba contra PostgreSQL real y ensayo de las siete fases en local. Pendiente de merge |
| D3 | **El pipeline no publica el snapshot.** Nadie llama a `cm.publicar_snapshot`: `cm.snapshot_vigente` queda vacío y `cm.reachability` nunca queda vigente | El impacto se resuelve siempre por el CTE recursivo. Es correcto y cumple el presupuesto (ADR-020), pero la aceleración de KC-CM-11-2 no se usa en producción | CodeMapping: publicar al cerrar el snapshot, con prueba de que `cm.impacto(p_ruta => 'auto')` usa la ruta materializada |
| D4 | **`DEFINES` y `HAS_COLUMN` no se escriben como aristas.** La pertenencia va por las FK `cm.symbols.file_id` y `cm.db_columns.table_id` | El contrato `cm-grafo-v1` acepta esos tipos, pero filtrar por ellos devuelve vacío; `HAS_COLUMN` está en los tipos de dependencia del impacto sin aportar nada | Documentado en el README del paquete y en la guía de la API. Decidir si se materializan o se retiran del contrato en una v2 |
| D5 | **`USES_TABLE` sale del archivo, no del símbolo** (`file` → `db_table`), al contrario de lo que dice la descripción de `cm.edge_types` | El impacto de una tabla llega a archivos, no a funciones concretas | Database: corregir la descripción del catálogo; CodeMapping: valorar el detector por símbolo |
| D6 | **El esquema no restringe qué pares de nodos une cada arista.** `edges_kind_chk` admite cualquier kind en cada extremo, `cm.v_catalogo_grafo` hace un producto cartesiano y `cm.edges` no tiene FK a `cm.nodes` | Una arista con un extremo inexistente o un par absurdo no la rechaza la base | Database: tabla de pares válidos por tipo de arista y, si el coste de carga lo permite, FK compuesta a `cm.nodes` |
| D7 | **Sin comparación con Neo4j ni medición con el dump real del cliente medio** | La retirada de Neo4j se apoya en benchmarks sintéticos calibrados, no en una comparación directa | Ya registrada en ADR-020. Neo4j está retirado; la comparación queda como evidencia histórica pendiente |
| D8 | **La rama a ingerir no es configurable**: siempre `main` | Un cliente con otra rama principal no se puede ingerir sin cambios | CodeMapping: añadir `target_branch` a `Settings` (`ingest_repo` ya lo busca) |
| D9 | **Nadie publica el evento de ingesta desde el onboarding**: el worker existe, pero el productor no | La primera ingesta de un cliente es manual | Backend / orquestador |
| D10 | **La reconstrucción determinista pierde las descripciones LLM de los símbolos** | Hay que restaurarlas del backup o pagar otra ingesta completa | Ya planteada a Database en el runbook (§3.6, §8) |
| D11 | **El refresco de `cm.reachability` es el coste dominante a escala** (40–100 s a 200.000 nodos) | Alarga cada publicación de snapshot | Seguimiento en el benchmark nocturno (ADR-020) |
| D12 | **RPO y RTO del esquema `cm` propuestos, no aprobados** (5 min / 60 min) | El ensayo cumple, pero el objetivo no es firme | Acuerdo con Database en `core.objetivos_recuperacion` |

## Consecuencias

- **Positivas (confirmadas):** un solo almacén, un solo modelo de aislamiento y una sola copia de seguridad con PITR;
  el grafo se cruza con curación, incidentes y vectores en la misma transacción; la reconstrucción completa de un tenant
  cabe holgadamente en el RTO propuesto.
- **Negativas (confirmadas):** los recorridos profundos dependen de límites explícitos (k ≤ 5, 5.000 nodos) y de
  `cm.reachability` para el caso caliente; la integridad del grafo descansa más en el cargador que en el esquema (D6).
- **Para GA:** D2 es bloqueante; D3 es recomendable antes de GA; el resto puede quedar como deuda registrada.

## Aprobación

| Rol | Nombre | Fecha | Decisión |
|---|---|---|---|
| Tech Lead CodeMapping | | | |
| Database (DDL, D5, D6, D10, D12) | | | |
