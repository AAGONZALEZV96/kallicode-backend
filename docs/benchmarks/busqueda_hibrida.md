# Búsqueda semántica híbrida: línea base y benchmark (KC-CM-08-4)

`cm.buscar_hibrido(embedding, capas, modulo, k, incluir_sin_curar)` (migración
`db/migrations/043_busqueda_hibrida.sql`) busca por similitud coseno sobre las capas de definiciones
(`vec.definition_embeddings`), documentación (`vec.doc_chunks`) y nuggets (`cm.nuggets`, por el
embedding del chunk que los respalda), con filtros relacionales por tenant, capa y módulo. Contrato:
`kallicode_core.cm_busqueda` (cm-busqueda-v1); cliente psycopg: `kallicode_codemapping.cm_queries`.

## Garantías

| Garantía | Cómo | Prueba |
|---|---|---|
| Solo filas del tenant de la sesión | `tenant_id = core.current_tenant()` explícito en cada recorrido vectorial (además de RLS); la consulta se planifica por llamada (`EXECUTE ... USING`), así que el tenant llega como constante y poda las particiones LIST de 037 | `tests/test_busqueda.py`: el EXPLAIN de la consulta real (`cm.explicar_busqueda_hibrida`) tiene el filtro en cada recorrido de `vec.*`; dos organizaciones con los mismos ids y vectores no se ven |
| Sin curar solo bajo petición | Conceptos: curado = `curado_por` o confianza >= `cm.umbral_confianza()` sin revisión pendiente (`cm.concepto_curado`). Nuggets: `aprobado`; `propuesto` solo con `incluir_sin_curar`; `rechazado` nunca | `tests/test_busqueda.py` (con y sin la petición; un concepto con revisión pendiente) |
| El filtro no hunde el recall | `vec.configurar_busqueda('diseno')` (038): `iterative_scan = relaxed_order`, `ef_search` 400, `max_scan_tuples` 5000, calibrado para recall@10 >= 0,90 con un tenant del 1 % | benchmark de 200.000 vectores (abajo) |
| No altera la transacción del llamante | `SET hnsw.*` en la función: PostgreSQL restaura los tres parámetros al salir | `tests/test_busqueda.py` |
| Orden determinista | similitud descendente; empate por capa e id (collation "C") | `tests/test_busqueda.py` |

Módulo: prefijo de ruta con semántica de carpeta (`src/billing` casa `src/billing` y
`src/billing/x`, nunca `src/billingx`). En definiciones se aplica a la columna `modulo`; un chunk está
en el módulo si está enlazado (`cm.doc_chunk_links`) a un fichero, a un símbolo de un fichero o a una
definición del módulo; un nugget, si lo está su chunk.

## Línea base de P@5 (v1)

Versionada en [`busqueda_hibrida_p5_v1.json`](busqueda_hibrida_p5_v1.json). Corpus de 109 elementos
en 10 temas de dominio (`packages/codemapping/tests/fixtures/busqueda/corpus.json`) y 20 consultas de
referencia (`consultas.json`), dos por tema, con y sin filtros. Una consulta considera relevantes los
elementos de su tema que la búsqueda puede devolver con sus filtros y su curación.

- **Métrica:** P@5 = relevantes entre los 5 primeros devueltos / 5. Un hueco cuenta como fallo, así
  que con menos de 5 relevantes el máximo alcanzable es menor que 1 (columna «máx.»).
- **Embebedor:** `ngramas-hash-v1`, determinista y sin red (n-gramas de caracteres en 1024
  dimensiones), para que la CI reproduzca la medida sin TEI. Mide filtros, curación y orden de la
  búsqueda con una similitud léxica; **no** es la calidad semántica de BGE-M3.
- **Resultado:** P@5 media **0,81** (máximo alcanzable 0,90).

| id | consulta | filtros | relevantes | P@5 | máx. |
|---|---|---|---|---|---|
| q01 | dónde se implementa la facturación por convenio corporativo | — | 10 | 1,00 | 1,00 |
| q02 | descuento pactado en el contrato de empresa al emitir la factura | capas: symbol, definition; módulo: src/facturacion | 4 | 0,80 | 0,80 |
| q03 | reintentos de cobro cuando la pasarela de pago rechaza la tarjeta | — | 11 | 1,00 | 1,00 |
| q04 | qué reglas aplican a los cobros fallidos | capas: nugget, doc | 4 | 0,40 | 0,80 |
| q05 | cálculo del IVA y retenciones en las facturas | — | 10 | 0,80 | 1,00 |
| q06 | tabla donde se guardan los tipos impositivos | capas: db_table, db_column, definition | 3 | 0,40 | 0,60 |
| q07 | inicio de sesión con doble factor de autenticación | — | 10 | 1,00 | 1,00 |
| q08 | bloqueo de la cuenta tras intentos fallidos de contraseña | incluye sin curar | 12 | 1,00 | 1,00 |
| q09 | envío de correos electrónicos al cliente cuando cambia el estado del pedido | — | 10 | 1,00 | 1,00 |
| q10 | plantillas de email e idioma de las notificaciones | módulo: src/notificaciones | 10 | 1,00 | 1,00 |
| q11 | reserva de stock al confirmar un pedido | — | 8 | 1,00 | 1,00 |
| q12 | qué es una reserva de inventario | capas: concept, definition | 2 | 0,40 | 0,40 |
| q13 | registro inmutable de eventos de auditoría | — | 10 | 1,00 | 1,00 |
| q14 | quién cambió un registro y cuándo | capas: doc, nugget | 4 | 0,40 | 0,80 |
| q15 | lista de precios y tarifas por producto | — | 9 | 1,00 | 1,00 |
| q16 | vigencia de una tarifa y cambios de precio programados | módulo: src/catalogo | 9 | 1,00 | 1,00 |
| q17 | conciliación bancaria de los movimientos con las facturas cobradas | — | 8 | 1,00 | 1,00 |
| q18 | conceptos del dominio de la conciliación bancaria | capas: concept; incluye sin curar | 3 | 0,60 | 0,60 |
| q19 | exportar informes de ventas a CSV o PDF | — | 9 | 0,80 | 1,00 |
| q20 | programar el envío periódico de reportes | capas: symbol, file, doc | 5 | 0,60 | 1,00 |

Los fallos son los esperables de una similitud léxica: q04 trae las «reglas» de conciliación, q14 los
«cambios» de notificaciones y q20 el otro `programar_*` (catálogo).

**Cómo se comprueba.** `tests/test_busqueda.py` siembra el corpus en PostgreSQL y mide P@5 con
`cm.buscar_hibrido` como `kallicode_app`: falla si la media baja de la línea base (tolerancia 0,01, un
empate en la quinta posición por el redondeo a float4) o si cambia más de una consulta.
`test/test_busqueda_unit.py` comprueba que el JSON se regenera idéntico desde el corpus.

**Regenerar** (solo cuando cambien a propósito el corpus, las consultas o el embebedor; un cambio de
consultas exige v2 de ambos ficheros):

```bash
uv run python -m kallicode_codemapping.evaluacion_busqueda --actualizar
# Con BGE-M3 (stack local levantado con make up):
uv run python -m kallicode_codemapping.evaluacion_busqueda --tei http://localhost:8080
```

**Pendiente:** medir la línea base con BGE-M3 (`--tei`) y versionarla junto a esta. No se pudo
generar todavía: la CI no tiene TEI y no se ha medido en un entorno con el modelo.

## Benchmark de 200.000 vectores

`packages/codemapping/tests/bench_busqueda_hibrida.py`, en `.github/workflows/busqueda_hibrida_nocturno.yml`
(cada noche y en los PR que tocan la búsqueda). Carga en las tablas **reales** 180.000 vectores en
`vec.definition_embeddings` (70 % definiciones, 30 % conceptos, de ellos un 20 % sin curar) y 20.000
en `vec.doc_chunks` (con enlaces al grafo y un nugget por cada cuatro chunks), repartidos entre diez
organizaciones: una del 10 % del corpus, una del 1 % y ocho más. Vectores alrededor de 100
centroides (coseno ~0,56 dentro de un grupo, ~0,03 entre grupos; semilla fija).

Mide, para el tenant del 10 % y el del 1 %, 200 consultas por `cm_queries.buscar_hibrido` como
`kallicode_app` (todas las capas; solo definiciones; solo conceptos; documentación y nuggets; con
módulo; conceptos con sin curar), y falla si:

- la latencia **p95 llega a 400 ms** (se informa también frente al objetivo de diseño de 80 ms);
- el **recall@10** con filtro de capa frente al top-10 exacto baja de **0,90**;
- algún recorrido vectorial del plan real (`EXPLAIN ANALYZE`) no filtra por tenant.

El informe (latencias, recall y nodos del plan, con el índice usado) queda en el resumen del job y en
el artefacto `busqueda-hibrida`.
