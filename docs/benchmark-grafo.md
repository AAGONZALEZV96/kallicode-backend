# Benchmark del grafo a 17.323 y 200.000 nodos (KC-DB-11-3)

Benchmark `packages/core/tests/bench_cm_khop.py` · CI nocturno `.github/workflows/grafo_nocturno.yml` ·
resultado de referencia `docs/benchmarks/grafo_khop_v1.json` · decisión `docs/adr/ADR-020-grafo-en-postgresql.md`.

## Conclusión

- **El recorrido a tres saltos cumple con holgura a 200.000 nodos.** El p95 de `impacto` es 16 ms y el
  de `dependencias` 1,8 ms por el CTE recursivo, frente a un presupuesto de 120 ms. La ruta elegida es
  la recursiva para las dos consultas y en los dos perfiles (criterio 2).
- **Las otras seis consultas críticas** quedan por debajo de 70 ms de p95, frente a los 500 ms del
  contrato.
- **Se encontró y corrigió un fallo de `aristas_entre`**: con nodos centrales agotaba el
  `statement_timeout` de producción (sección 4).
- **Falta la comparación con Neo4j** (criterio 3): CodeMapping no ha entregado sus cifras (sección 5).
- **Verificado con el grafo del cargador real de KC-CM-11-1** (193.800 nodos estructurales): las ocho
  consultas cumplen y la ruta elegida sigue siendo la recursiva (sección 7). Ese grafo es más uniforme
  que la proyección y sus p95 son mejores; la proyección sigue siendo el caso exigente.
- **El refresco de `cm.reachability` es el riesgo a esta escala**, no las consultas: de 40 a 58 s con
  la base limpia, y unos 100 s con `cm.*` hinchada, dentro de la transacción de `publicar_snapshot`
  (secciones 3 y 7).
- **Hallazgo para CodeMapping:** su cargador no escribe aristas `HAS_COLUMN` (ni `DEFINES`), así que en
  producción el impacto de una columna no llega a su tabla (sección 7.3).

## 1. Qué se mide y cómo

Las ocho consultas críticas del ADR-020 (`kallicode_core.cm_impacto.CONSULTAS`), por la ruta de
producción:

- **Sesión:** `kallicode_app` con RLS y el tenant en la sesión, la SQL de
  `kallicode_codemapping.cm_queries` y el mismo `statement_timeout` (5 s) que el cliente real.
- **`impacto` y `dependencias`:** van por `cm.impacto()`, como en producción desde KC-CM-11-2. Se miden
  sus dos rutas: el CTE recursivo y `cm.reachability`.
- **Parámetros:** k = 3, y k = 2 para el subgrafo, como en el contrato. `aristas_entre` recibe los ids
  del subgrafo de cada semilla, como hace `cm_queries.subgrafo`.
- **Semillas:** 200 por consulta, tras 20 de calentamiento. El 95 % son símbolos, tablas y columnas al
  azar; el 5 % son los nodos de mayor grado de entrada (los centrales), que son el peor caso del
  impacto y pesan en el p95.
- **Ruido:** un segundo tenant con 17.323 nodos en las mismas tablas, para que el filtro por tenant
  cuente.
- **Entorno:** PostgreSQL 16.16 con la imagen del CI (`db/postgres`, `shared_buffers = 1GB`) en Docker,
  sobre una máquina de 12 CPU. Las cifras del runner del CI pueden ser algo peores; el job nocturno las
  publica en su resumen.

### Datasets

| Perfil | Nodos | Aristas | DEFINES | CALLS | USES_TABLE | HAS_COLUMN | REFERENCES | Máx. grado de entrada |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `cliente_medio` | 17.323 | 23.309 | 13.800 | 5.500 | 1.600 | 2.100 | 309 | 193 |
| `proyeccion_200k` | 200.000 | 300.000 | 160.000 | 94.000 | 19.000 | 23.400 | 3.600 | 1.905 |

Forma del grafo:

- Ficheros con unos 11,5 símbolos cada uno y tablas con unas 9,4 columnas.
- Las `CALLS` y `USES_TABLE` siguen una distribución de Zipf (α = 0,8) en el destino, así que hay
  nodos muy centrales.
- Solo el 30 % de los símbolos accede a datos, y hay ciclos.

**Límite importante: el «dataset real» es sintético.** El ticket cita el grafo del cliente medio, con
17.323 nodos y 23.309 aristas, pero ese dump no está disponible. El perfil `cliente_medio` reproduce
exactamente esas cifras con un generador determinista. Cuando CodeMapping entregue el dump,
`KC_BENCH_GRAFO_DIR=<dir>` con `nodos.csv` (`id,kind[,padre]`) y `aristas.csv` (`tipo,src_id,dst_id`) lo
mide como un perfil más, junto a los sintéticos, sin cambiar nada más (`leer_csv`, probado con un
viaje de ida y vuelta; formato en la sección 6).
Hasta entonces, el criterio 1 está cubierto con el perfil calibrado, no con el dump.

El ticket mencionaba también un sintético anterior de 20.000 nodos. No existe en el repositorio. El
grafo de referencia del contrato es `grafo_sintetico()`, con unos 4.350 nodos y 21.000 aristas, mucho
más denso que el cliente medio: 4,8 aristas por nodo frente a 1,35.

## 2. Resultados (2026-11-15, en milisegundos)

### Recorrido a tres saltos (criterio 2: p95 < 120 ms)

| Perfil | Consulta | Ruta | p50 | p95 | p99 | máx. | p95 centrales | Filas medias |
|---|---|---|---:|---:|---:|---:|---:|---:|
| cliente_medio | impacto | **recursiva** | 6,2 | 8,8 | 12,6 | 13,4 | 11,8 | 10,9 |
| cliente_medio | impacto | materializada | 6,9 | 8,4 | 10,2 | 11,8 | 11,3 | 10,9 |
| cliente_medio | dependencias | **recursiva** | 7,8 | 10,7 | 12,0 | 15,6 | 7,9 | 5,4 |
| cliente_medio | dependencias | materializada | 8,6 | 11,2 | 11,7 | 12,0 | 9,6 | 5,4 |
| proyeccion_200k | impacto | **recursiva** | 1,2 | 16,1 | 43,3 | 77,7 | 64,4 | 80,5 |
| proyeccion_200k | impacto | materializada | 2,4 | 9,6 | 29,0 | 46,1 | 39,1 | 80,5 |
| proyeccion_200k | dependencias | **recursiva** | 1,2 | 1,8 | 2,5 | 3,0 | 2,7 | 6,4 |
| proyeccion_200k | dependencias | materializada | 2,4 | 3,0 | 3,2 | 3,6 | 3,3 | 6,4 |

Ningún resultado se truncó ni agotó el timeout.

### Las otras seis consultas (presupuesto del contrato: p95 < 500 ms)

| Consulta | p95 cliente_medio | p95 proyeccion_200k | máx. proyeccion_200k |
|---|---:|---:|---:|
| resolver_nodos | 1,3 | 1,2 | 1,6 |
| semillas | 10,9 | 1,9 | 2,2 |
| subgrafo_nodos (k = 2) | 12,0 | 29,2 | 122,5 |
| aristas_entre | 4,5 | 68,1 | 111,9 |
| vecinos | 1,3 | 1,4 | 5,1 |
| dependencias_ocultas | 11,1 | 4,1 | 18,7 |

La tabla completa, con p50, p99 y los estratos al azar y centrales, está en el resumen del job y en
`docs/benchmarks/grafo_khop_v1.json`.

### Planes

El CTE de `impacto` recorre `cm.edges` solo por `edges_in_ix`, y el de `dependencias` solo por
`edges_out_ix`, en los dos perfiles. No hay ningún Seq Scan sobre `cm.edges`. El benchmark falla si
eso cambia.

## 3. Decisión sobre `cm.reachability` (criterio 2)

- **Ruta para el recorrido a tres saltos:** la recursiva cumple el presupuesto en las dos consultas y
  los dos perfiles, así que **no hace falta recurrir a `cm.reachability`** para cumplirlo.
  `cm.impacto(p_ruta => 'auto')` sigue usando la tabla cuando está vigente. Con nodos centrales a
  200.000 nodos es más rápida (p95 de 39 ms frente a 64 ms), y fuera de ellos cuesta alrededor de 1 ms
  más. Las dos rutas dan el mismo resultado (KC-DB-11-2).
- **Coste de la tabla a 200.000 nodos:** 938.330 filas y 1.021.185 caminos, un 20 % del presupuesto
  de 5.000.000. Su refresco alarga `publicar_snapshot` hasta **41 s**, dentro de la transacción de
  publicación. Con 17.323 nodos son 66.136 filas y 2,6 s.
- **Confirmado con KC-CM-11-1 (sección 7.2): el refresco es un riesgo para `publicar_snapshot`.** Con
  el grafo del cargador real cuesta 39,6 s dentro de su ingesta y 42,9 s en el benchmark con la base
  limpia. Pero el coste depende también de lo que hay en las tablas compartidas `cm.*`: 57,9 s para
  publicar la proyección con otro tenant de 200.000 nodos ya cargado, y unos 100 s con las tablas
  hinchadas por tenants borrados. Por encima de 60 s dentro de la transacción de publicación, se
  propone **un refresco diferido o en dos fases** (ticket aparte, a acordar con CodeMapping): publicar
  el snapshot, dejar `cm.reachability` en un estado no vigente y refrescarla después. Mientras tanto,
  `cm.impacto` cae al CTE, que cumple el presupuesto en los tres grafos medidos.

## 4. Hallazgo corregido: `aristas_entre` con planes genéricos

**Síntoma.** En la primera medición, `aristas_entre` agotó el `statement_timeout` de 5 s en 8 de las
200 semillas de la proyección y en 1 del cliente medio. Su p95 fue de 722 ms. En producción esas
llamadas a `subgrafo` habrían fallado.

**Causa.** La consulta filtraba `src_id = ANY(:ids) AND dst_id = ANY(:ids)`. psycopg prepara la
sentencia en el servidor tras cinco ejecuciones, y a partir de ahí PostgreSQL puede pasar a un plan
genérico que no conoce el tamaño de `:ids`. Ese plan evalúa el array entero por cada arista candidata.
Mismo caso (5.000 ids, proyección):

| Ejecución | Tiempo |
|---|---:|
| Sin preparar (plan a medida) | 121 ms |
| Preparada, `force_custom_plan` | 105 ms |
| Preparada, `force_generic_plan` | > 60 s |

`cm_queries` usa una conexión de larga vida (`GraphLoader`), así que producción estaba expuesta igual.

**Corrección** (`SQL_ARISTAS_ENTRE` en `kallicode_core/cm_impacto.py`). Los ids se desanidan una vez,
sin repetidos (`WITH ids AS MATERIALIZED (SELECT DISTINCT ...)`), y se cruzan con un `JOIN` por
`edges_out_ix` y un semi-join con hash para el destino. El contrato no cambia: mismas columnas, orden
y límite.

**Verificación:**
- 0 diferencias de filas frente a la consulta original en las 200 semillas de cada perfil, más un caso
  con ids repetidos.
- Con plan genérico, el caso de 5.000 ids tarda 94 ms.
- En el benchmark, el p95 de la proyección baja de 722 ms a 68 ms, sin timeouts.
- **Pruebas nuevas:**
  - `packages/core/tests/test_cm_impacto.py::test_aristas_entre_cruza_los_ids_por_join_y_no_por_any`,
    que impide que vuelva el `= ANY(:ids)`;
  - `packages/codemapping/tests/test_impacto.py::test_aristas_entre_con_miles_de_ids_en_plan_generico`,
    que comprueba la equivalencia con la referencia en memoria con 3.000 ids y repetidos, con plan
    genérico y con plan a medida.

## 5. Comparación con Neo4j (criterio 3: pendiente)

Este criterio queda abierto: las cifras de Neo4j las aporta CodeMapping y no se han entregado. Para la
comparación hacen falta las mismas ocho consultas sobre el mismo grafo: el perfil `cliente_medio` o el
dump real, exportable con `bench_cm_khop.py --exportar <dir>`. Hay que medir p50 / p95 / p99 con las
mismas semillas.

| Consulta | PostgreSQL p95 (cliente_medio) | PostgreSQL p95 (200k) | Neo4j p95 |
|---|---:|---:|---:|
| impacto (k = 3) | 8,8 | 16,1 | pendiente |
| dependencias (k = 3) | 10,7 | 1,8 | pendiente |
| subgrafo_nodos (k = 2) | 12,0 | 29,2 | pendiente |
| dependencias_ocultas | 11,1 | 4,1 | pendiente |

## 6. Cómo reproducirlo

```bash
# Con las migraciones aplicadas (ver el workflow para el arranque de PostgreSQL)
uv run pytest packages/core/tests/bench_cm_khop.py -v -s                     # criterios
uv run python packages/core/tests/bench_cm_khop.py --json resultado.json     # informe + JSON
KC_BENCH_KHOP_PERFILES=cliente_medio uv run pytest packages/core/tests/bench_cm_khop.py -s
KC_BENCH_GRAFO_DIR=/ruta/al/dump uv run pytest packages/core/tests/bench_cm_khop.py -s
KC_BENCH_GRAFO_DIR=/ruta/al/dump KC_BENCH_GRAFO_NOMBRE=cm_11_1 \
  uv run pytest packages/core/tests/bench_cm_khop.py -s                     # dump como perfil propio
```

**El dump es un perfil más.** `KC_BENCH_GRAFO_DIR` añade el dump a los dos perfiles sintéticos, no los
sustituye. `KC_BENCH_GRAFO_NOMBRE` le da nombre en el informe (por defecto `dataset_real`; no puede
coincidir con un perfil sintético). `KC_BENCH_KHOP_PERFILES` sigue permitiendo elegir qué se mide.

**Formato del dump.** `nodos.csv` (`id,kind[,padre]`) y `aristas.csv` (`tipo,src_id,dst_id`), solo la
capa estructural. El padre de símbolos y columnas sale de las aristas `DEFINES` / `HAS_COLUMN` o, si
no están, de la columna opcional `padre`. Desde la base, con el padre de las FK:

```bash
psql ... -c "\copy (SELECT n.id, n.kind, COALESCE(s.file_id, c.table_id) AS padre FROM cm.nodes n
  LEFT JOIN cm.symbols s ON n.kind = 'symbol' AND s.tenant_id = n.tenant_id AND s.id = n.id
  LEFT JOIN cm.db_columns c ON n.kind = 'db_column' AND c.tenant_id = n.tenant_id AND c.id = n.id
  WHERE n.tenant_id = '<t>' AND n.kind IN ('file','symbol','db_table','db_column'))
  TO 'nodos.csv' CSV HEADER"
psql ... -c "\copy (SELECT tipo, src_id, dst_id FROM cm.edges WHERE tenant_id = '<t>'
  AND tipo IN ('DEFINES','CALLS','USES_TABLE','HAS_COLUMN','REFERENCES')) TO 'aristas.csv' CSV HEADER"
```

Las rutas de los ficheros no se exportan (`leer_csv` las rellena como `dump/<id>`); solo afecta a la
consulta `semillas` por fichero.

**Estado de la base.** El refresco de `cm.reachability` es sensible a la hinchazón de `cm.*`. Para
comparar tiempos de publicación entre corridas, hacer `VACUUM (FULL, ANALYZE)` de `cm.*` antes de medir
si se han borrado tenants grandes.

Disco: la corrida completa carga unos 235.000 nodos y 323.000 aristas, más casi un millón de filas de
`cm.reachability`. Necesita varios GB libres en el volumen de Docker.

## 7. Verificación con el grafo de KC-CM-11-1 (2026-11-15)

El grafo lo genera el **cargador real de CodeMapping**, no el generador de este benchmark:

- **Origen:** `repo_sintetico.Escala.completa()` ingestado con `build_graph.full_build`, seguido del
  refresco diario de 200 archivos (`bench_ingesta_escala.py`, PR #280). El tenant se conservó con
  `KC_BENCH_CONSERVAR` y su capa estructural se exportó a CSV. El padre de símbolos y columnas sale de
  `cm.symbols.file_id` / `cm.db_columns.table_id` (sección 7.3) y no se añadió ninguna arista.
- **Medición:** perfil `cm_11_1`, junto a los dos sintéticos, en la misma corrida y con el mismo
  método (sección 1). Resultado en `docs/benchmarks/grafo_khop_cm_11_1_v1.json`; la referencia de la
  sección 2 (`grafo_khop_v1.json`) no se sobrescribió.

### 7.1 Cargas

| Perfil | Nodos | Aristas | CALLS | USES_TABLE | HAS_COLUMN | REFERENCES | DEFINES | Máx. grado de entrada |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `proyeccion_200k` (referencia) | 200.000 | 300.000 | 94.000 | 19.000 | 23.400 | 3.600 | 160.000 | 1.905 |
| `cm_11_1` | 193.800 | 239.400 | 214.200 | 24.000 | 0 | 1.200 | 0 | 42 |

- Son 193.800 nodos y no 200.200 porque se excluye la capa semántica: 6.000 definiciones y 400
  conceptos, con sus 20.800 aristas `REALIZED_BY`, `EXPRESSES` y `RELATES`. Las ocho consultas no la
  recorren.
- **La forma es muy distinta a la de la proyección.** Hay 1,1 llamadas por símbolo, pero con
  desplazamientos fijos (`i → i + 1` y `i → i + n/2 + 1`), así que no hay nodos centrales: el grado de
  entrada máximo es 42, frente a 1.905. Las cadenas `CALLS` son largas y casi sin ramificación.

### 7.2 Resultados (p95 en ms, 200 semillas)

| Consulta | proyeccion_200k (referencia, sección 2) | proyeccion_200k (esta corrida) | cm_11_1 | Presupuesto |
|---|---:|---:|---:|---:|
| impacto k = 3, recursiva (**elegida**) | 16,1 | 14,5 | **4,3** | 120 |
| impacto k = 3, materializada | 9,6 | 23,4 | 10,0 | 120 |
| dependencias k = 3, recursiva (**elegida**) | 1,8 | 1,7 | **1,9** | 120 |
| dependencias k = 3, materializada | 3,0 | 11,4 | 2,5 | 120 |
| resolver_nodos | 1,2 | 1,1 | 1,1 | 500 |
| semillas | 1,9 | 2,5 | 2,6 | 500 |
| subgrafo_nodos (k = 2) | 29,2 | 27,1 | 3,5 | 500 |
| aristas_entre | 68,1 | 64,8 | 1,5 | 500 |
| vecinos | 1,4 | 1,5 | 1,0 | 500 |
| dependencias_ocultas | 4,1 | 3,3 | 2,6 | 500 |

- **Las ocho consultas cumplen.** Ninguna se truncó y no hubo timeouts: `aristas_entre` dio 0 de 200.
  Los planes del CTE usan solo `edges_in_ix` / `edges_out_ix`, sin Seq Scan, en los tres perfiles.
- **La ruta elegida no cambia.** Es la recursiva para `impacto` y para `dependencias` en los tres
  perfiles (criterio 2).
- **Las cifras de `cm_11_1` son mejores que las de la proyección.** Sin nodos centrales, el impacto
  medio devuelve 12,8 filas, frente a 80,5. La proyección sigue siendo el caso exigente y es la que
  queda como puerta del CI.
- En esta corrida, la ruta materializada salió más lenta en la proyección que en la referencia (23,4
  frente a 9,6 ms). Coincide con el estado hinchado de `cm.*` de la tabla siguiente; no cambia la
  decisión, porque la recursiva es la elegida.

**Coste de `cm.reachability`** (refresco dentro de `publicar_snapshot`):

| Grafo | Estado de la base | Filas | Caminos | Refresco |
|---|---|---:|---:|---:|
| proyeccion_200k (referencia) | limpia | 938.330 | 1.021.185 | 40,9 s |
| cm_11_1, dentro de su ingesta (KC-CM-11-1) | limpia | 744.091 | 1.117.932 | 39,6 s |
| cm_11_1 | limpia (`VACUUM FULL`) | 744.091 | 1.117.932 | 42,9 s |
| proyeccion_200k, con cm_11_1 ya cargado | limpia más otro tenant de 200.000 | 938.330 | 1.021.185 | 57,9 s |
| proyeccion_200k / cm_11_1, corrida principal | hinchada por tenants borrados sin VACUUM | — | — | 105,0 / 100,0 s |

Las dos tablas siguen `vigente`, muy por debajo del presupuesto de 5.000.000 caminos. **El tiempo
crece con el contenido de las tablas compartidas `cm.*`, no solo con el tenant que se publica**, y
supera los 60 s en cuanto conviven dos tenants grandes o hay hinchazón. La propuesta está en la
sección 3: refresco diferido o en dos fases, en un ticket aparte.

### 7.3 Hallazgo para CodeMapping: no hay aristas `HAS_COLUMN` ni `DEFINES`

El cargador de CodeMapping (`graph_loader.py`) no escribe ninguna arista `DEFINES` ni `HAS_COLUMN`. La
pertenencia solo queda en `cm.symbols.file_id` y `cm.db_columns.table_id`.

- **`DEFINES` no afecta a las consultas.** Ningún recorrido la usa y las semillas por fichero van por
  `file_id`.
- **`HAS_COLUMN` sí.** Es uno de los cuatro tipos de dependencia de `cm.impacto` y de
  `cm.reachability` (`TIPOS_DEPENDENCIA`, migración 059). Sin ella, en un grafo ingestado de verdad
  las 9.000 columnas quedan aisladas: el impacto de cambiar una columna no llega a su tabla, ni a los
  ficheros con `USES_TABLE` sobre esa tabla, ni a lo que dependa de ellos.
- **Decisión para el benchmark (Tech Lead):** el padre se toma de las FK (columna `padre` del CSV) sin
  sintetizar aristas, para medir el grafo tal como queda hoy en producción. Que el cargador escriba
  `HAS_COLUMN` (y `DEFINES`, por coherencia con el catálogo de la migración 021) se decide con CM4.
