# Recuperación del esquema `cm` y reconstrucción concurrente de los índices HNSW (KC-DB-11-4)

Migración `064_recuperacion_cm.sql` · herramientas `db/tools/ensayo_reconstruccion_cm.py` y
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
-- 4. ANALYZE por fase (SELECT * FROM cm.analizar_tablas()) y, DESPUÉS, la alcanzabilidad: cm.publicar_snapshot(...) o
--    cm.refrescar_reachability() con app.tenant_id fijado (sin ANALYZE previo puede tardar minutos)
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
prueba, borra su capa determinista con `cm.vaciar_grafo_tenant`, la **reconstruye** (COPY del grafo, `ANALYZE` por fase con
`cm.analizar_tablas` y alcanzabilidad con `cm.publicar_snapshot`) y compara los recuentos **por kind de nodo y por tipo de
arista** con los de antes. Los tres escenarios **cuadran** (recuentos idénticos).

**Sobre el «dataset real».** El ticket cita el grafo del cliente medio (17.323 nodos y 23.309 aristas), pero ese dump
**nunca existió**: el equipo usa como tal el perfil `cliente_medio` de KC-DB-11-3 (`packages/core/tests/bench_cm_khop.py`,
`docs/benchmark-grafo.md`), un generador determinista y calibrado que reproduce exactamente esas cifras, igual que
`proyeccion_200k` (200.000 nodos y 300.000 aristas). El ensayo usa **esos mismos perfiles**, así que los números son
comparables con los del benchmark. Si algún día aparece un dump real, `KC_BENCH_GRAFO_DIR=<dir>` (con `nodos.csv` y
`aristas.csv`) lo carga en lugar de `cliente_medio` sin tocar nada más.

| Escenario | Nodos | Aristas | Vaciado | **Reconstrucción total** | Cuadra |
|---|---|---|---|---|---|
| Perfil `cliente_medio` (el «dataset real» del ticket) | 17.323 | 23.309 | 18,7 s | **18,6 s** | sí |
| Perfil `proyeccion_200k` | 200.000 | 300.000 | 59,7 s | **242,6 s** | sí |
| Repositorio **real** (este repo, ingesta determinista con tree-sitter) | 7.025 | 35.022 | 4,9 s | **8,1 s** | sí |

Desglose de la reconstrucción del perfil de 200.000 nodos: COPY del grafo 131,2 s · `ANALYZE` por fase 0,3 s ·
`cm.reachability` **110,9 s** (938.330 filas, 1.021.185 caminos) · `ANALYZE` de `cm.reachability` 0,2 s. Para `cliente_medio`:
COPY 8,2 s · `ANALYZE` 0,1 s · alcanzabilidad 10,1 s (66.136 filas). El parseo del repositorio real con tree-sitter, no
incluido arriba, tardó 4,8 s.

Los recuentos comparados incluyen `nodo:file`, `nodo:symbol`, `nodo:db_table`, `nodo:db_column`, `arista:DEFINES`,
`arista:CALLS`, `arista:HAS_COLUMN`, `arista:USES_TABLE`, `arista:REFERENCES` y las filas de `cm.reachability`.

**Hay que hacer `ANALYZE` de las tablas del grafo antes de `cm.refrescar_reachability()`.** En una primera ejecución sin ese
paso, el refresco sobre el grafo del repositorio real superó los 8 minutos y hubo que cancelarlo; con `ANALYZE` previo, las
ejecuciones siguientes terminaron en segundos. KC-CM-11-1 lo midió con detalle (`db/migrations/062_cm_analizar_por_fase.sql`:
1,5 s con estadísticas frente a 63 s sin ellas, ×42) y creó `cm.analizar_tablas()`, que este ensayo usa. El procedimiento de §3
lo incluye.

**Qué cuenta el «tiempo de reconstrucción».** Cargar el grafo ya extraído, `ANALYZE` y alcanzabilidad: lo que cuesta
reconstruir `cm` **en la base**. No incluye la fase de LLM ni los embeddings (TEI); la capa semántica y los vectores se
restauran del backup. Tampoco es el tiempo de parsear un repositorio de 200.000 nodos (el parseo medido es el del repositorio
real, de 6.339 símbolos).

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

* **El «dataset real» es el perfil calibrado `cliente_medio` de KC-DB-11-3**: el dump del cliente medio nunca existió. Es
  sintético (determinista, con la forma de un repositorio real), no un grafo de un cliente. Si aparece un dump real, el ensayo
  lo admite con `KC_BENCH_GRAFO_DIR` y bastará repetir la medición.
* **Los perfiles no incluyen el tiempo de parsear un repositorio de ese tamaño**: el grafo ya está extraído. El parseo
  medido es el de un repositorio real de 6.339 símbolos.
* **No se mide la fase de LLM ni los embeddings (TEI)**: la capa semántica y los vectores se restauran del backup.
* **HNSW con 10⁶ filas con carga concurrente: no se ha completado en este equipo** (RAM insuficiente: ~4,5 KB de
  `maintenance_work_mem` por fila y ~80 min por millón; ver `docs/runbook-vectorial.md` §6-7). El ensayo admite
  `--filas 1000000 --mwm 5GB` y debe repetirse en un servidor dimensionado. El criterio de 10⁶ filas **queda pendiente** de
  esa ejecución.
* **Reconstrucción en el sitio**: no hay conmutación por snapshot (ver §3).
* Docker local, un solo tenant, tiempos del procedimiento, no una promesa de producción.
