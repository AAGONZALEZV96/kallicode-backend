# Recuperación del esquema `cm` y reconstrucción concurrente de los índices HNSW (KC-DB-11-4)

Migración `062_recuperacion_cm.sql` · herramientas `db/tools/ensayo_reconstruccion_cm.py` y
`db/tools/ensayo_hnsw_reconstruccion.py` · pruebas `packages/core/tests/integracion/test_recuperacion_cm.py` ·
continuación de la recuperación ante desastre de `docs/recuperacion_pitr.md` (KC-DB-11-1) y del runbook
`docs/runbook-vectorial.md` (KC-DB-08-2).

## 1. Por qué una política además del PITR

El PITR restaura **todo** a un instante. No siempre es lo que hace falta: el grafo de **un** tenant puede corromperse
(una ingesta defectuosa, un parser con un fallo) mientras el resto está bien, o la copia disponible puede no llegar al
momento que interesa. Entonces hay que saber **qué se puede volver a producir y qué no**: lo que costó tokens de LLM,
decisiones humanas o es historia no se reproduce igual, y lo que sale de un repositorio con un parser determinista sí.

## 2. La política (`core.politica_recuperacion`)

Una fila por tabla de `cm` y `vec` y por índice HNSW. Una prueba exige que **toda tabla de `cm` y `vec` esté
clasificada** (`core.tablas_sin_politica_recuperacion()` debe devolver cero filas): una tabla nueva obliga a decidir.

| Estrategia | Qué significa | Qué entra |
|---|---|---|
| **reconstruir** | Se vuelve a producir desde su fuente; el backup no es imprescindible | `cm.files`, `cm.symbols`, `cm.db_tables`, `cm.db_columns`, `cm.nodes`, `cm.reachability` (+ `_estado`), catálogos sembrados por migraciones, `vec.hnsw_catalogo` y los **tres índices HNSW** |
| **restaurar** | La copia de seguridad es la fuente | Capa semántica y humana (`cm.definitions`, `cm.concepts`, `cm.nuggets`, `cm.curation_queue`, `cm.doc_chunk_links`), historia (`cm.repo_snapshots`, `cm.snapshot_vigente`, `cm.incidents`, `cm.test_flows`, `cm.realimentacion_tickets`…) y los vectores (`vec.*_embeddings`, `vec.doc_chunks`) |
| **mixta** | Una parte y otra | `cm.edges`: `DEFINES`, `CALLS`, `HAS_COLUMN`, `REFERENCES`, `USES_TABLE` se reconstruyen; `REALIZED_BY`, `RELATES`, `EXPRESSES` (semánticas) se restauran |

El detalle de cada fila (fuente, coste y motivo) está en la propia tabla: `SELECT * FROM core.politica_recuperacion`.

**Los vectores se restauran y no se reconstruyen** aunque sean reproducibles: recalcular los embeddings de cada nodo
con BGE-M3 cuesta inferencia (horas a escala) y no es necesario si hay backup. La vía de reparación sigue disponible
(`embeddings_a_reparar`, KC-CM-11-4).

**Un índice nunca se restaura por separado**: es derivado de su tabla y se reconstruye siempre (§5).

## 3. Reconstruir el grafo de UN tenant

```sql
-- 1. recuentos de partida
SELECT * FROM cm.recuentos_grafo('org_xxx');
-- 2. borrar la capa determinista (exige repetir el tenant)
SELECT * FROM cm.vaciar_grafo_tenant('org_xxx', 'determinista', 'org_xxx');
-- 3. reingerir: CM_TENANT_ID=org_xxx python -m kallicode_codemapping.build_graph (ingesta completa)
-- 4. cm.refrescar_reachability() con app.tenant_id fijado, y ANALYZE de cm.*
-- 5. comparar con los recuentos del paso 1: deben ser idénticos por kind y por tipo de arista
SELECT * FROM cm.recuentos_grafo('org_xxx');
```

| Alcance | Borra | No toca |
|---|---|---|
| `determinista` | archivos, símbolos, tablas y columnas del cliente, `CALLS`/`DEFINES`/`HAS_COLUMN`/`REFERENCES`/`USES_TABLE`, `cm.reachability` | capa semántica, historia, snapshots |
| `completo` | lo anterior más definiciones, conceptos y aristas semánticas | historia operativa (incidentes, flujos de prueba, realimentación, cola de curación, nuggets, snapshots) |

**Advertencias:**

* **Los embeddings de los nodos borrados se borran en cascada** (`vec.definition_embeddings` referencia `cm.nodes` con
  `ON DELETE CASCADE`). Reconstruir la capa determinista invalida los embeddings de los símbolos de ese tenant: hay que
  restaurarlos del backup o recalcularlos. Los ids son deterministas (`uuid5`), así que los símbolos reingeridos recuperan
  los mismos ids y lo que se restaure vuelve a enlazar.
* **El tenant se queda sin grafo mientras se reconstruye** (borrado en el sitio). Para no cortar el servicio habría que
  construir en un snapshot nuevo y conmutar `cm.snapshot_vigente`; **eso no está hecho**.
* Solo el rol administrador puede ejecutar estas funciones. Se serializan por tenant con un bloqueo consultivo.

## 4. Lo medido (`db/tools/ensayo_reconstruccion_cm.py`)

Comando: `python db/tools/ensayo_reconstruccion_cm.py --salida <dir>`. Para cada escenario carga el grafo de un tenant de
prueba con el **cargador real** (`GraphLoader`, COPY por fase), borra su capa determinista con `cm.vaciar_grafo_tenant`,
la **reconstruye** (archivos, símbolos y llamadas, esquema, `ANALYZE`, `cm.refrescar_reachability`) y compara los
recuentos **por kind de nodo y por tipo de arista** con los de antes. Los tres escenarios **cuadran** (recuentos idénticos).

| Escenario | Archivos | Símbolos | Vaciado | **Reconstrucción total** | Cuadra |
|---|---|---|---|---|---|
| Repositorio **real** (este repo, ingesta determinista) | 670 | 6.134 | 0,4 s | **10,1 s** | sí |
| Sintético de **17.323 nodos / 23.305 aristas** (tamaño del dataset real) | 692 | 16.478 | 1,9 s | **10,7 s** | sí |
| Sintético de **200.000 nodos / 299.886 aristas** | 8.000 | 190.200 | 8,6 s | **113,4 s** | sí |

Desglose de la reconstrucción de 200.000 nodos: archivos 0,7 s · símbolos y llamadas 28,1 s · esquema 0,9 s ·
`ANALYZE` 1,1 s · `cm.reachability` **82,7 s** (1.392.558 filas, 1.590.502 caminos). La alcanzabilidad es la fase más cara
en los tres. El parseo del repositorio real con tree-sitter (no incluido arriba) tardó 2,5 s.

Los recuentos comparados incluyen `nodo:file`, `nodo:symbol`, `nodo:db_table`, `nodo:db_column`, `arista:CALLS`,
`arista:USES_TABLE`, `arista:REFERENCES` y las filas de `cm.reachability` (el informe completo por escenario sale del ensayo).

**Hallazgo: hay que hacer `ANALYZE` de las tablas del grafo antes de `cm.refrescar_reachability()`.** En una primera
ejecución sin ese paso, el refresco sobre el grafo del repositorio real superó los 8 minutos y hubo que cancelarlo; en otra,
un refresco de 200.000 nodos agotó un tope de 120 s. Con `ANALYZE` previo, las tres ejecuciones siguientes terminaron en
segundos (repositorio real: 5-8 s). Lo atribuyo a la falta de estadísticas tras la carga masiva (el planificador elige
planes malos con tablas recién cargadas), **sin haberlo demostrado con `EXPLAIN`**. Coincide con el «`ANALYZE` por fase» que pide
KC-CM-11-1, y el procedimiento de §3 lo incluye.

**Qué cuenta el «tiempo de reconstrucción».** Cargar el grafo ya extraído, `ANALYZE` y alcanzabilidad: lo que cuesta
reconstruir `cm` **en la base**. No incluye la fase de LLM ni los embeddings (TEI); la capa semántica y los vectores se
restauran del backup. Tampoco es el tiempo de parsear un repositorio de 200.000 nodos (el parseo medido es del repositorio real,
de 6.134 símbolos).


## 5. Reconstrucción concurrente de los tres índices HNSW (`db/tools/ensayo_hnsw_reconstruccion.py`)

La orden es la que programa pg_cron: `REINDEX INDEX CONCURRENTLY vec.<índice>`. No bloquea escrituras ni lecturas;
construye el índice nuevo junto al existente y lo intercambia. Requisitos y procedimiento completos:
`docs/runbook-vectorial.md`.

Comando: `python db/tools/ensayo_hnsw_reconstruccion.py --filas 20000 --mwm 1GB`. Carga 20.000 vectores en **cada una**
de las tres tablas, arranca una carga concurrente (un hilo que inserta y actualiza en las tres tablas y otro que busca por
similitud) y reconstruye los tres índices con `REINDEX INDEX CONCURRENTLY`, uno tras otro. Se mantuvo la carga durante
toda la reconstrucción (unos 11 minutos).

| Índice | Reconstrucción | Escrituras durante (p50 / p99 / máx) | Búsquedas durante (p50 / p99 / máx) | Válido |
|---|---|---|---|---|
| `ticket_embeddings_hnsw_ix` | 138,7 s | 547 (159 / 1.133 / 13.157 ms) | 2.684 (16 / 219 / 10.617 ms) | sí |
| `doc_chunks_hnsw_ix` (particionado) | 312,5 s | 1.700 (139 / 309 / 14.181 ms) | 7.539 (16 / 39 / 11.159 ms) | sí |
| `definition_embeddings_hnsw_ix` (particionado) | 215,4 s | 1.348 (122 / 286 / 4.088 ms) | 5.738 (13 / 36 / 2.266 ms) | sí |

* **Errores de consulta durante toda la reconstrucción: 0** (3.621 ciclos de escritura y 16.034 búsquedas en total).
* Los tres índices quedan **válidos**, sin restos de reconstrucciones a medias (`_ccnew` / `_ccold`), y las búsquedas
  siguen planificándose con el índice HNSW.
* **No se detiene el servicio, pero hay paradas puntuales:** una escritura o una búsqueda aislada llegó a tardar entre 2 y 14 s
  (coincide con lo medido en KC-DB-08-2: la construcción satura el disco y una operación espera). El p99 de las búsquedas
  sigue en decenas de milisegundos salvo en `ticket_embeddings`. Por eso la reconstrucción se programa en horas valle
  (`docs/runbook-vectorial.md`).
* Recall@10 contra la búsqueda exacta, **solo informativo** (los vectores son aleatorios): antes 0,88 · 0,89 · 0,90 y
  después 1,0 en las tres. Con vectores aleatorios el recall depende de cómo se construyó el índice (un índice crecido por
  inserciones sueltas lo tiene peor que uno reconstruido); la calidad con embeddings reales la vigila
  `recall_vectorial_nocturno.yml`.
* Cargar los 60.000 vectores iniciales tardó **1.156 s** (unos 52 vectores/s) porque cada inserción actualiza el índice HNSW
  existente: es la razón por la que no es viable cargar 10⁶ filas por esta vía en este equipo.

**Criterio de 10⁶ filas: NO cumplido aquí.** El equipo de pruebas (Docker con ~5,7 GB de RAM) no admite el
`maintenance_work_mem` que haría falta (~4,5 GB para 10⁶ filas; `docs/runbook-vectorial.md` §7) ni completa en un tiempo
razonable una construcción de ese tamaño (KC-DB-08-2 ya lo intentó). El ensayo es el mismo con
`--filas 1000000 --mwm 5GB`, pero debe ejecutarse en un servidor dimensionado: hasta entonces el criterio sigue **abierto**.
Lo medido (20.000 filas por tabla, tres índices, cero errores) prueba la mecánica y la ausencia de errores, no la
escala del ticket.


## 6. Qué NO está hecho ni medido

* **El dataset real de CodeMapping (17.323 nodos y 23.309 aristas) no se ha usado**: no está en el repositorio. Se midió
  con un grafo sintético de ese tamaño y con la ingesta real de este repositorio. Cuando esté disponible, el mismo ensayo
  lo admite (`ensayo_reconstruccion_cm.py`), y hay que repetir la medición.
* **El generador de 200.000 nodos es propio y sintético**; el de KC-CM-11-1 (carga masiva con COPY y repositorio sintético)
  no estaba entregado. Al estarlo, conviene repetir la medición con él.
* **No se mide la fase de LLM ni los embeddings (TEI)**: la capa semántica y los vectores se restauran del backup.
* **HNSW con 10⁶ filas con carga concurrente: no se ha completado en este equipo** (RAM insuficiente: ~4,5 KB de
  `maintenance_work_mem` por fila y ~80 min por millón; ver `docs/runbook-vectorial.md` §6-7). El ensayo admite
  `--filas 1000000 --mwm 5GB` y debe repetirse en un servidor dimensionado. El criterio de 10⁶ filas **queda pendiente** de
  esa ejecución.
* **Reconstrucción en el sitio**: no hay conmutación por snapshot (ver §3).
* Docker local, un solo tenant, tiempos del procedimiento, no una promesa de producción.
