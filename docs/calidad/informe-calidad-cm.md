# Calidad semántica del grafo de CodeMapping (KC-CM-12-4)

Informe para el expediente de release. Cifras medidas el 17 de noviembre de 2026 sobre el SHA de la
rama `feature/e1-kc-cm-12-4-calidad-semantica`. Desde la integración, el job nocturno
`calidad_semantica_nocturno.yml` publica una medición nueva cada noche (ver
[Serie temporal](#serie-temporal)).

## Resumen

| Criterio | Resultado |
|---|---|
| Precisión > 0,85 y exhaustividad > 0,80 sobre el conjunto de evaluación | **Se cumple, con poco margen.** Con BGE-M3, el modelo de producción: precisión **0,862** y exhaustividad **0,855** (media de las 50 preguntas). La primera medición, antes de los arreglos de este ticket, dio 0,763 y 0,782 (ver [De 0,763 a 0,862](#de-0763-a-0862)). |
| Conjunto y script en el repositorio, en el CI nocturno con la serie temporal publicada | **Se cumple.** `packages/codemapping/tests/evaluacion/` y `kallicode_codemapping.evaluacion_calidad`. El job nocturno añade cada noche un punto al artefacto `serie-calidad-cm` y muestra los diez últimos en el resumen de la ejecución. La CI de cada PR también corre la parte que no depende de TEI. |
| Desviaciones y casos fallidos documentados con su hipótesis de causa | **Se cumple.** 20 de las 50 preguntas no aciertan del todo. Sus causas se agrupan en siete desviaciones, cada una con su hipótesis y una propuesta (ver [Desviaciones](#desviaciones)). |
| Informe incorporado al expediente de release | Enlazado desde [`docs/release/expediente.md`](../release/expediente.md). La aceptación de GA del expediente es de KC-DB-12-5. |

## Qué se mide y cómo

**Repositorio de evaluación.** `packages/codemapping/tests/evaluacion/repo_eval` es una mini
aplicación de facturación con 21 ficheros:
- backend en Python, con SQLAlchemy y SQL crudo;
- portal en TypeScript y TSX;
- un esquema de 9 tablas con 5 FK.

Tiene a propósito los patrones que hacen fallar a un grafo estático: homónimos en el mismo
lenguaje y en lenguajes distintos, métodos llamados sobre objetos, constructores de modelos ORM,
una CTE que no es una tabla, despacho por diccionario, closures y callbacks, y un `.tsx` que llama
a un `.ts`. Su README explica qué caso cubre cada fichero.

**Preguntas.** `tests/evaluacion/preguntas.yaml` tiene 50 preguntas con respuesta conocida:

| Operación | Preguntas | Qué pregunta |
|---|---:|---|
| `impacto` | 22 | Quién depende de un símbolo, fichero o tabla, a 1-3 saltos |
| `dependencias` | 12 | De qué depende un símbolo, fichero o tabla, a 1-3 saltos |
| `dependencias_ocultas` | 6 | Quién comparte tablas con el área sin que se llegue a él por llamadas |
| `busqueda` | 10 | Qué símbolos responden a una consulta en lenguaje natural |

La respuesta de cada pregunta está **escrita a mano leyendo el código**. No se deriva del grafo,
porque eso sería circular. Las reglas de la respuesta están en el README de `repo_eval`:
- k saltos por las aristas de dependencia del contrato `cm-impacto-v1`;
- USES_TABLE de fichero a tabla;
- los closures con nombre pertenecen también a su contenedor;
- los anónimos no se listan;
- las semillas no cuentan.

Ningún caso fallido se resolvió cambiando la respuesta.

**Ruta.** El grafo se construye por la ruta real del pipeline:
1. `parse_file`;
2. `GraphLoader.load_files/load_symbols`;
3. `detectar_uso_tablas` y `load_schema`;
4. embeddings de los símbolos con el texto canónico (`texto_embedding_simbolo`);
5. `cm.refrescar_reachability()`.

Las preguntas se hacen como `kallicode_app`, con RLS, mediante `cm_queries`. Solo se sustituye el
LLM: la descripción de cada símbolo, que en producción escribe la fase 4, es la de
`repo_eval/descripciones.yaml`, escrita a partir del código y sin mirar las consultas.

**Métricas.** Por pregunta, precisión = aciertos / devueltos y exhaustividad = aciertos /
esperados. El criterio usa la media macro de las 50 preguntas; la media micro es informativa.
- Una respuesta vacía cuando se esperaba algo vale 0 y 0.
- La búsqueda usa R-precisión: k = número de esperados, así que precisión = exhaustividad.
- `impacto` y `dependencias` se preguntan por las dos rutas de `cm.impacto()` (`cm.reachability` y
  CTE recursivo). Las dos responden igual en las 34 preguntas.

**Embebedor.** El criterio se mide con **BGE-M3** (TEI 1.8 en CPU), el modelo de producción. La CI
de PR no tiene TEI y usa `ngramas-hash-v1`, un embebedor léxico determinista. Con él, la búsqueda
baja de 0,767 a 0,650: no mide calidad semántica, solo protege contra regresiones.

## Resultados

| Operación | Preguntas | Precisión | Exhaustividad |
|---|---:|---:|---:|
| `impacto` | 22 | 0,920 | 0,908 |
| `dependencias` | 12 | 0,889 | 0,842 |
| `dependencias_ocultas` | 6 | 0,750 | 0,833 |
| `busqueda` (BGE-M3) | 10 | 0,767 | 0,767 |
| **Total (media macro)** | **50** | **0,862** | **0,855** |
| Total (media micro) | 50 | 0,928 | 0,904 |
| Las 40 de impacto (sin búsqueda) | 40 | 0,886 | 0,877 |
| Total con `ngramas-hash-v1` (CI de PR) | 50 | 0,839 | 0,831 |

El margen sobre el umbral de precisión es de 0,012. Equivale a una sola pregunta de búsqueda que
pase de acertar dos de tres a acertar una de tres. La serie nocturna es la que dirá si es estable.

## De 0,763 a 0,862

La primera medición se hizo con el pipeline de develop y el embebedor de CI: precisión 0,763 y
exhaustividad 0,782. El Tech Lead aprobó arreglar en este ticket tres defectos acotados:

| Arreglo | Dónde | Preguntas que corrige |
|---|---|---|
| **A.** Entre homónimos gana el del mismo fichero y después el de un fichero importado; solo sin ninguno de los dos se enlazan todos, como antes | `resolucion_llamadas.py` y la resolución de `GraphLoader.write_symbols` | IMP-04, IMP-05, IMP-21, DEP-02, DEP-06 y DEP-07 (dos `validar` en Python) |
| **B.** TypeScript, TSX y JavaScript forman una familia: un `.tsx` resuelve llamadas a un `.ts` | ídem | IMP-13, IMP-15, IMP-16 y DEP-09 (en parte) |
| **C.** Crear la instancia de un modelo SQLAlchemy (`LineaFactura(...)` + `session.add`) es un uso de su tabla | `detectores_orm._sqlalchemy` | IMP-19, IMP-20 y DEP-11 |

| Medición | Precisión | Exhaustividad |
|---|---:|---:|
| develop, `ngramas-hash-v1` | 0,763 | 0,782 |
| con A + B + C, `ngramas-hash-v1` | 0,839 | 0,831 |
| con A + B + C, BGE-M3 | **0,862** | **0,855** |

Cada arreglo tiene pruebas propias:
- `test/test_resolucion_llamadas_unit.py` (rutas importadas por lenguaje);
- `tests/test_resolucion_llamadas_pg.py` (mismo fichero, import relativo, import absoluto como sufijo
  de ruta, sin información y familia JS, contra PostgreSQL).

La suite de `orm_ref` sigue en verde con C. La resolución sigue en un solo `INSERT … SELECT`, con
los imports en una tabla temporal indexada y analizada.

Coste en la carga: con el repositorio sintético de KC-CM-11-1 a 44.000 símbolos, `load_symbols` tarda
9,2 s, frente a 9,5 s con develop, y produce las mismas 53.500 aristas CALLS (dos mediciones de cada
uno). Ese repositorio no tiene homónimos ni imports, así que la medición muestra el sobrecoste fijo,
no el peor caso. La ingesta a 200.000 nodos la sigue vigilando `ingesta_escala_nocturno.yml`.

## Desviaciones

Son las 20 preguntas que no aciertan del todo con BGE-M3, agrupadas por causa. Las de búsqueda
cambian con el embebedor; las demás no dependen de él.

### D1. Despacho dinámico: `MOTORES[formato](filas)`

**Preguntas:** IMP-11 (P 0, E 0), DEP-04 (E 0,600) y DEP-05 (P 0, E 0).

**Qué pasa:** `exportar` elige el motor en un diccionario de funciones. El analizador solo extrae
invocados que son identificadores o atributos. Un subíndice no da nombre, así que `exportar` no
depende de nadie, y `render_pdf` y `render_html` parecen código muerto.

**Hipótesis:** un grafo estático por nombres no sigue funciones guardadas en estructuras de datos.

**Propuesta:** una arista de referencia cuando un nombre de función aparece como valor en un
literal (dict, lista o tupla) del mismo módulo y ese literal se invoca con subíndice.

### D2. Métodos sobre objetos de tipo desconocido: `session.get()` frente a `Cache.get`

**Preguntas:** IMP-12 (P 0,250), DEP-01, DEP-02, DEP-03 y DEP-11 (un falso positivo cada una).

**Qué pasa:** `session.get(Factura, id)` es de SQLAlchemy, pero se llama `get`, igual que el método
de `api/cache.py`. Ni el mismo fichero ni los imports dan un candidato mejor, así que el arreglo A
mantiene el comportamiento anterior y enlaza con `Cache.get`. Lo hace a propósito: si el método
pertenece a un objeto inyectado cuyo módulo no se importa (`self.repo.guardar()`), descartarlo
perdería una arista real.

**Hipótesis:** sin inferencia de tipos, los nombres de método muy comunes (`get`, `add`, `save`,
`update`) son ambiguos entre el código del cliente y sus librerías.

**Propuesta:**
- medir sobre repositorios reales cuántas aristas de rango 2 son llamadas a atributo con nombres
  comunes;
- valorar no enlazarlas, o marcarlas con menor confianza.

### D3. Callbacks y closures: sus llamadas no se atribuyen al contenedor

**Preguntas:** IMP-13 (E 0,800), IMP-15 (E 0,667), IMP-16 (E 0,500) y DEP-09 (E 0,500).

**Qué pasa:** `valores.map((v) => formatearMoneda(v))` y el closure `pagar` de `PantallaPago` son
símbolos propios. El analizador aísla sus llamadas a propósito: no las suma al contenedor.

- El callback anónimo no tiene llamadores, así que `formatearLista` no aparece como dependiente
  de `formatearMoneda`.
- `PantallaPago` solo llama a `pagar` desde el manejador de un evento (otro anónimo), así que no
  aparece como dependiente de `enviarPago`.

**Hipótesis:** el aislamiento es correcto para funciones anidadas con nombre que se exportan, pero
no para los callbacks inline, que son parte del cuerpo del contenedor.

**Propuesta:** atribuir al contenedor las llamadas de las funciones anónimas que define, o una
arista CALLS contenedor → closure cuando el closure se define en su cuerpo.

### D4. `dependencias_ocultas` compara ficheros con los símbolos alcanzados

**Preguntas:** OCU-01 y OCU-05 (P 0,750, un falso positivo cada una).

**Qué pasa:** `facturacion/modelos.py` aparece como oculto aunque el área llama a sus símbolos
(`Pago`, `total_bruto`). USES_TABLE sale del nodo fichero. La consulta solo excluye los nodos
alcanzados, y el fichero no lo está aunque sus símbolos sí.

**Hipótesis:** desajuste de granularidad en `SQL_DEPENDENCIAS_OCULTAS`
(`kallicode_core.cm_impacto`).

**Propuesta:** excluir los ficheros que tengan algún símbolo en `alcance_min`. Es un cambio del
contrato `cm-impacto-v1`, que coordina KC-CM-12-5.

### D5. `dependencias_ocultas` desde un símbolo devuelve siempre vacío

**Pregunta:** OCU-06 (P 0, E 0).

**Qué pasa:** con un símbolo como semilla, ningún nodo alcanzado tiene USES_TABLE, porque esa arista
es del fichero. La consulta no encuentra tablas.

**Hipótesis:** la misma granularidad que en D4, en el otro sentido.

**Propuesta:** tomar las tablas de los ficheros de los símbolos alcanzados (`cm.symbols.file_id`).

### D6. Búsqueda: el vocabulario del dominio frente a la paráfrasis

**Preguntas:** BUS-06 (0,333) y BUS-09 (0,333).

**Qué pasa:**
- BUS-06, «clientes que no han pagado sus facturas»: devuelve `obtenerFacturas` y `pagarFactura`
  del portal, que comparten las palabras literales, en lugar de `informe_mensual` y `get_morosos`.
  Esas dos se describen con el término del dominio («morosidad», «morosos»).
- BUS-09, «exportar el informe de morosidad en PDF»: «informe de morosidad» domina la consulta y
  trae `informe_mensual` y `tarea_informe_csv` por delante de `exportar` y `render_pdf`.

**Hipótesis:** sobre la capa `symbol` y con descripciones de una línea, BGE-M3 no tiende el
puente entre la paráfrasis y el término del dominio. Ese puente debería salir de las capas
`definition` y `concept` (glosario), que este conjunto no evalúa.

**Propuesta:** una segunda versión del conjunto con preguntas sobre las capas de definiciones y
conceptos, cuando la fase de ontología tenga un fixture fijo.

### D7. Búsqueda: un tercer resultado vecino

**Preguntas:** BUS-02, BUS-03 y BUS-05 (0,667, uno de tres fuera).

**Qué pasa:**
- BUS-02, «enviar un correo electrónico al cliente»: entra `enviarPago` (verbo compartido) y sale
  `notificar_pago`.
- BUS-03, «calcular el IVA de una factura»: `total_bruto` le gana a `tasa_vigente`, cuya
  descripción no menciona ni el IVA ni la factura.
- BUS-05, «conciliar los movimientos del banco con los pagos»: `notificar_pago` le gana a
  `registrar_pago`.

**Hipótesis:** el tercer puesto lo deciden diferencias muy pequeñas de similitud entre
descripciones cortas y parecidas. La descripción real del LLM, más larga, puede ordenarlas de otra
manera.

**Propuesta:** volver a medir cuando la fase 4 tenga casete, con descripciones del LLM en lugar de
las del fixture.

## Serie temporal

`.github/workflows/calidad_semantica_nocturno.yml` corre a las 05:45 UTC y en los PR que tocan el
pipeline, la evaluación o su línea base. Hace esto:
1. Arranca PostgreSQL y TEI con BGE-M3 (`--auto-truncate --max-batch-tokens 1024`; con lotes de
   8.192 tokens el calentamiento se quedó sin memoria en 7,7 GB).
2. Descarga el artefacto `serie-calidad-cm` de la última ejecución que lo publicó.
3. Mide y añade un punto: fecha, SHA, embebedor, métricas globales y por operación, y preguntas
   fallidas.
4. Lo vuelve a publicar con retención de 90 días.
5. **Bloquea** si se da cualquiera de estas condiciones:
   - el total no supera los umbrales;
   - alguna pregunta baja de su línea base;
   - las dos rutas de `cm.impacto()` no coinciden.

Los PR miden, pero no añaden puntos.

La línea base por pregunta está en `docs/calidad/linea_base_calidad_cm.json`: el grafo, más la
búsqueda con cada embebedor. Se regenera a mano cuando cambian a propósito el conjunto, el fixture o
el pipeline:

    uv run python -m kallicode_codemapping.evaluacion_calidad --actualizar-linea-base [--tei http://localhost:8080]

En la CI de cada PR (`ci.yml`, proyecto codemapping), `tests/test_calidad_semantica.py` exige:
- las 40 preguntas de impacto por encima de los umbrales;
- ninguna regresión sobre la línea base de `ngramas-hash-v1`;
- las dos rutas iguales.

## Reproducir

    make up && make db
    docker run -d -p 8080:80 ghcr.io/huggingface/text-embeddings-inference:cpu-1.8 \
        --model-id BAAI/bge-m3 --auto-truncate --max-batch-tokens 1024
    uv run python -m kallicode_codemapping.evaluacion_calidad --tei http://localhost:8080 \
        --resumen-md resumen.md
    KC_EVAL_TEI_URL=http://localhost:8080 uv run pytest packages/codemapping/tests/test_calidad_semantica.py -v
