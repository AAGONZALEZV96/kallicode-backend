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
`KC_BENCH_GRAFO_DIR=<dir>` con `nodos.csv` (`id,kind`) y `aristas.csv` (`tipo,src_id,dst_id`) lo carga en
lugar del perfil sintético, sin cambiar nada más (`leer_csv`, probado con un viaje de ida y vuelta).
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
- **A vigilar** cuando llegue la carga de 200.000 nodos de KC-CM-11-1: si los 41 s de bloqueo al
  publicar son un problema, conviene un refresco en dos fases o diferido. En ese caso `cm.impacto`
  cae al CTE, que cumple de todas formas.

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
```

Disco: la corrida completa carga unos 235.000 nodos y 323.000 aristas, más casi un millón de filas de
`cm.reachability`. Necesita varios GB libres en el volumen de Docker.
