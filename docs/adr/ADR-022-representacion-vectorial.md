# ADR-022: Representación vectorial de las colecciones de `vec.*` (float4, halfvec, cuantización binaria)

- **Estado:** aceptado (KC-DB-08-3, hallazgo DB-F31)
- **Decisión en una línea:** **se mantiene `vector(1024)` (float4)** en las tres colecciones; halfvec queda preparado y
  ensayado pero no se aplica, y la cuantización binaria con reordenación exacta queda como la salida cuando la
  memoria obligue.

## Contexto

`vec.ticket_embeddings`, `vec.doc_chunks` y `vec.definition_embeddings` guardan `vector(1024)` (BGE-M3, float4): 4 KB por
fila más el grafo HNSW (m = 16, ef_construction = 64, coseno). El índice guarda una copia de cada vector, así que el
coste de memoria es de unos 7,8 KB de índice por fila (medido). Con la documentación y las definiciones de muchos
clientes el índice deja de caber en memoria y la latencia se degrada. Nadie había medido si halfvec (float2) o la
cuantización binaria con búsqueda en dos fases (índice sobre 1 bit por dimensión y reordenación exacta de los
candidatos con el vector completo) son una salida aceptable.

## Qué se midió y cómo

`packages/core/tests/bench_halfvec.py`: mismo corpus, mismas 200 consultas, mismo HNSW real (m = 16,
ef_construction = 64) en las tres representaciones; recall@10 frente al top-10 **exacto en float4** (sin índice), p95 de
latencia y tamaño de tabla e índice (`pg_table_size`, `pg_indexes_size`). Se mide en el `ef_search` que usa Diseño
(400, `vec.parametros_busqueda`) y en 100. Se añade halfvec **sin índice** para separar lo que cuesta el formato
float2 de lo que cuesta el grafo.

**Corpus (limitación importante).** Los vectores semilla son **reales**: embeddings BGE-M3 generados con el modelo de
producción a partir de textos del repositorio (277 trozos de `docs/` para `doc_chunks`, 350 docstrings de Python para
`definition_embeddings`, 157 comentarios de las migraciones SQL para `ticket_embeddings`; no hay tickets reales en el
repositorio). Como son pocos, se **amplían a 50.000 filas por colección** mezclando pares de semillas con ruido. La
distribución del modelo es la real, pero los vecinos cercanos de cada fila son sintéticos y muy densos. Es un corpus
semi-real: los resultados orientan, no sustituyen a una medición con datos de un cliente.

### Resultados (50.000 filas por colección, `ef_search = 400`)

| Colección | Representación | recall@10 | p95 (ms) | Tabla | Índice | Bytes/fila (tabla + índice) |
|---|---|---|---|---|---|---|
| `doc_chunks` | **vector (float4)** | **0,992** | 24,9 | 267 MB | 391 MB | 13.785 |
| | halfvec con HNSW | 0,952 | 18,4 | 135 MB | 130 MB | 5.561 |
| | halfvec **sin índice** (solo el redondeo) | 0,998 | 399,5 | 135 MB | 130 MB | 5.561 |
| | binaria + reordenación exacta ×50 | 0,884 | 15,3 | 266 MB | 21 MB | 6.019 |
| | binaria + reordenación exacta ×200 | **0,998** | 20,7 | 266 MB | 21 MB | 6.019 |
| `definition_embeddings` | **vector (float4)** | **0,998** | 24,9 | 267 MB | 391 MB | 13.785 |
| | halfvec con HNSW | 0,957 | 17,1 | 135 MB | 130 MB | 5.561 |
| | halfvec sin índice | 0,997 | 415,6 | 135 MB | 130 MB | 5.561 |
| | binaria ×200 | **0,999** | 21,1 | 266 MB | 21 MB | 6.019 |
| `ticket_embeddings` | **vector (float4)** | **0,985** | 23,8 | 267 MB | 391 MB | 13.785 |
| | halfvec con HNSW | 0,930 | 16,7 | 135 MB | 130 MB | 5.561 |
| | halfvec sin índice | 0,999 | 422,9 | 135 MB | 130 MB | 5.561 |
| | binaria ×200 | **0,991** | 19,2 | 266 MB | 21 MB | 6.019 |

(Cifras de una corrida de `bench_halfvec.py --tabla`. La latencia a 50.000 filas es de 17–18 ms en halfvec y 24–25 ms
en float4, con un índice que en ambos casos cabe en la caché del sistema; en una máquina de desarrollo con Docker esa
diferencia es ruido, se espera que la ventaja de latencia aparezca cuando el índice float4 deje de caber en memoria y el de halfvec no; eso no se midió.)

Lo que dicen los números:

1. **El formato float2 no es el problema, el grafo sí.** Con el redondeo a float2 y búsqueda exacta, la pérdida de
   recall es de **0,1 a 0,3 puntos** (< 1). Pero con el índice HNSW de halfvec el recall cae **4 a 5 puntos** respecto
   al HNSW de float4 (0,93–0,96 frente a 0,985–0,998).
2. **No se arregla con parámetros.** En `ticket_embeddings`, subir `ef_search` a 600, 800 o 1000 deja halfvec en
   0,946–0,949 (float4 llega a 0,995–0,999); `ef_construction = 200` y `m = 32` con `ef_construction = 128` tampoco
   lo mueven (0,946–0,949). Tampoco depende de la construcción paralela (con 0 workers, 0,939).
3. **La cuantización binaria con reordenación ×200 no pierde recall** (0,991–0,999, igual o mejor que el HNSW de float4)
   y su índice es **18 veces menor** (21 MB frente a 391 MB), pero **la tabla no baja** porque se conserva el vector
   completo para reordenar. Con ×50 candidatos el recall cae 10–20 puntos: el sobremuestreo es obligatorio.
4. halfvec **sí reduce a la mitad la tabla (135 MB frente a 267 MB) y a un tercio el índice (130 MB frente a 391 MB)**:
   13.785 → 5.561 bytes por fila (−60 %).

No se pudo determinar la causa de la meseta de halfvec en el HNSW (el formato por sí solo no la explica: la búsqueda
exacta con halfvec da 0,997–0,999). La hipótesis más plausible es la densidad de vecinos casi idénticos del corpus
ampliado, que castiga más a un grafo construido con distancias en float2; un control con vectores gaussianos
aleatorios (30.000 filas) dio 0,557 en float4 y 0,539 en halfvec, que no discrimina (consulta demasiado difícil en 1024
dimensiones). **Está sin resolver**, por eso el criterio de reversión incluye volver a medir.

## Decisión

1. **Mantener `vector(1024)` y los HNSW con `vector_cosine_ops` en las tres colecciones.** El criterio de adopción
   del ticket (pérdida de recall@10 de halfvec < 1 punto) no se cumple en ninguna: 4,0 a 5,4 puntos al `ef_search` de
   producción.
2. **No adoptar la cuantización binaria todavía.** Iguala el recall, pero obliga a reescribir las consultas en dos fases
   (`dedup-preview`, `impact-preview`, búsqueda híbrida), mantiene un índice sobre una expresión y no reduce la tabla;
   hoy el índice cabe en memoria. Es la salida preferente cuando deje de caber (ver reversión).
3. **Dejar halfvec preparado y ensayado**, para que adoptarlo sea una operación conocida:
   - `db/migrations/045_hnsw_opclase.sql`: `vec.hnsw_catalogo.opclase`; `db/06_indices_concurrentes.sql` lee la clase de
     operadores del catálogo en vez de tenerla escrita.
   - `db/propuestas/halfvec/aplicar_halfvec.sql` y `revertir_halfvec.sql`: conversión y reversión por colección
     (retira el HNSW, `ALTER COLUMN ... TYPE halfvec(1024)`, reconstruye con `halfvec_cosine_ops`, adapta la consulta
     híbrida de `043`). Más de 100.000 filas: el índice se reconstruye con `06_indices_concurrentes.sql`.
   - Ensayo en `packages/core/tests/integracion/test_halfvec.py` (CI): con filas, dentro de una transacción que se
     deshace; comprueba que no se pierden filas, que el índice resulta válido, que el código actual
     (`CAST(:e AS vector)`) sigue usando el índice (el cast `vector → halfvec` es implícito) y que la reversión
     devuelve el esquema anterior.
4. **Vigilancia nocturna** (`.github/workflows/recall_vectorial_nocturno.yml`, `bench_halfvec.py`): falla si float4
   baja de 0,97 de recall@10, si el redondeo a float2 por sí solo pasa de un punto o si la binaria ×200 deja de igualar
   a float4; **avisa «REABRIR ADR-022»** (sin fallar) cuando halfvec con HNSW pase el criterio.

## Criterio de reversión (cuándo cambiar de representación)

| Disparador | Acción |
|---|---|
| El benchmark nocturno avisa «REABRIR ADR-022» (halfvec con HNSW pierde < 1 punto: nueva versión de pgvector, otro corpus) | Repetir con datos reales de un cliente; si se confirma, ejecutar `aplicar_halfvec.sql` por colección y actualizar este ADR. |
| El índice HNSW de una colección supera ~50 % de la memoria disponible para caché (`shared_buffers` + caché del sistema) **y** el p95 de la búsqueda se duplica | Primera opción: **binaria con reordenación ×200** (el índice se reduce 18×; requiere reescribir las consultas en dos fases). Segunda: particionar por tenant (ya hecho en 037) y construir por partición. halfvec solo si se acepta perder ~4 puntos de recall@10. |
| Se cambia el modelo de embeddings (otras dimensiones o valores fuera de ±65.504) | Repetir el benchmark; float2 no representa valores mayores. |
| Tras aplicar halfvec en producción el recall o los duplicados detectados bajan más de lo previsto | `revertir_halfvec.sql` (conserva los valores ya redondeados; para recuperar los bits originales hay que recalcular los embeddings o restaurar una copia). |

## Efecto en almacenamiento y en el coste de las copias

Por fila (medido a 50.000 filas): float4 13.785 B; halfvec 5.561 B (−60 %); binaria con reordenación 6.019 B (−56 %,
casi todo en el índice). Extrapolación lineal, **no medida**, por millón de filas y colección:

| | float4 (vigente) | halfvec | binaria + reordenación |
|---|---|---|---|
| Tabla + índice en disco | ≈ 13,8 GB | ≈ 5,6 GB | ≈ 6,0 GB |
| Memoria para tener el índice en caché | ≈ 7,8 GB | ≈ 2,6 GB | ≈ 0,4 GB (más páginas de tabla para reordenar) |
| Copia física (`pg_basebackup`, WAL) | proporcional a lo anterior | −60 % | −56 % |

La copia lógica (`pg_dump`) no incluye los índices: depende del tamaño del texto de los vectores, que con halfvec es
menor (menos cifras por valor), pero no se midió. Como se mantiene float4, **el coste de almacenamiento y de copias no
cambia con este ADR**; la tabla anterior es lo que se ahorraría al adoptar halfvec y lo que costaría seguir con float4
cuando crezca el corpus. Una reconstrucción de HNSW (`REINDEX CONCURRENTLY`, runbook-vectorial) también es proporcional:
la construcción de 50.000 filas tardó 32–66 s con halfvec y 40–66 s con float4 (sin diferencia relevante).

## Consecuencias

- **Positivas:** la decisión está medida y no se pierde recall; la alternativa (halfvec) queda lista y ensayada; el
  catálogo de índices ya soporta cambiar de clase de operadores; se vigila cada noche que la elección siga siendo
  correcta.
- **Negativas / riesgos:** se sigue pagando 13,8 KB por fila; el corpus del benchmark es semi-real y la causa de la meseta
  de halfvec no está explicada (pudo haber otro resultado con datos reales); la binaria con reordenación exige trabajo de
  aplicación que no se hace en este ticket.
