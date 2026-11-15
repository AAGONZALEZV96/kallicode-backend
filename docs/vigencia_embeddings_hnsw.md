# Mantenimiento de los HNSW y vigencia de los embeddings BGE-M3 (KC-CM-11-4)

Migración `db/migrations/060_vigencia_embeddings_hnsw.sql` · reparación
`packages/codemapping/src/kallicode_codemapping/vigencia.py` · métricas
`packages/codemapping/src/kallicode_codemapping/metricas.py` · benchmark `packages/core/tests/bench_hnsw_rotacion.py`
(CI nocturno) · pruebas `packages/codemapping/tests/test_vigencia_embeddings_pg.py` y
`packages/codemapping/test/test_vigencia_unit.py`.

El refresco diario de CodeMapping reescribe los embeddings de lo que cambió. Eso tenía dos efectos sin vigilar:

- **Vigencia:** nada garantizaba que el embedding de un nodo correspondiera a su descripción vigente.
- **Índices:** el HNSW se hinchaba y perdía recall.

Este documento explica:

- cómo se mide y se repara la vigencia;
- qué rutina de mantenimiento se programó y por qué cambian los parámetros del índice;
- qué se expone como métrica.

## 1. Vigencia de los embeddings

### 1.1 Qué significa «vigente»

`vec.definition_embeddings.texto_hash` es el hash de su propia columna `descripcion`, que es el texto que se
embebió. Compararlos dentro de la fila no prueba nada. La fuente de verdad son los nodos de `cm`.

| Capa | Texto canónico (lo que se embebe) | De dónde sale |
|---|---|---|
| `symbol` | `nombre firma descripción`, recortado | `cm.symbols (name, signature, descripcion)` |
| `definition` | `nombre descripción`, recortado | `cm.definitions (nombre, descripcion)` |

Un embedding es **vigente** si cumple dos condiciones:

- su `texto_hash` es `sha256(texto canónico)[:32]`, calculado **ahora** desde `cm.*`;
- su `modelo_embedding` es el modelo vigente (`bge-m3`).

En otro caso el nodo está **obsoleto** (por descripción o por modelo) o **sin embedding**.

**Fórmula única.** La fórmula está una sola vez en Python: `embeddings.texto_embedding_simbolo`,
`texto_embedding_definicion` y `hash_texto_embedding`. Ahora la usan también `build_graph` y `refresh`, que antes
duplicaban la f-string. En SQL existe la misma fórmula: `cm.texto_embedding_simbolo`, `cm.texto_embedding_definicion`
y `vec.hash_texto`.

**Paridad Python–SQL.** `str.strip()` de Python recorta 29 caracteres de espacio, y `cm.recortar_espacios` los
recorta todos. Una prueba recorre cada uno de esos caracteres y casos con saltos de línea, NBSP, emojis y
descripción nula o vacía. **La fórmula no cambió**, así que los hashes ya guardados siguen siendo válidos.

**La descripción LLM de los símbolos no se guardaba.** `cm.symbols.descripcion` existía, pero nadie la escribía.

- **Desde ahora:** `GraphLoader.actualizar_descripciones_simbolos` la guarda en el build completo y en el refresco.
- **Datos existentes:** la migración la recupera del texto ya embebido. Solo la escribe cuando, al recomponer el
  texto canónico, se obtienen **exactamente** el texto y el hash embebidos; si no, el nodo sale como obsoleto y se
  reembebe.

### 1.2 Medir

```sql
-- tenant de la sesión (kallicode_app, RLS)
SELECT * FROM vec.vigencia_embeddings();
--  capa | nodos | con_embedding | vigentes | obsoletos | sin_embedding | ratio
-- todos los tenants, solo cuentas (métrica del worker; SECURITY DEFINER)
SELECT * FROM vec.vigencia_global();
-- qué hay que reembeber y por qué (obsoleto | modelo | sin_embedding)
SELECT * FROM vec.embeddings_a_reparar(100);
```

### 1.3 Reparar: el paso de vigencia del refresco

El refresco incremental solo reembebe los símbolos de los ficheros que cambiaron. Hay casos que no cubre:

- una descripción curada a mano;
- una definición reconsolidada;
- un cambio de modelo;
- un embedding perdido.

Al final de cada refresco, también cuando no hay commits nuevos, `vigencia.reparar_vigencia` hace cuatro cosas:

1. lee `vec.embeddings_a_reparar`;
2. construye el texto con las funciones canónicas;
3. lo embebe en lotes TEI y lo escribe;
4. registra la vigencia resultante.

| Variable | Por defecto | Qué hace |
|---|---|---|
| `CM_VIGENCIA_MAX_REEMBEBIDOS` | 5000 | Nodos reembebidos por corrida (0 = solo medir). Lo que no cabe queda para la siguiente y se registra `pendientes` |

El paso **no es fatal**: si TEI falla, el refresco termina igual, porque el grafo ya está cargado. Lo no reparado
aparece como obsoleto en la métrica y se repara en la corrida siguiente.

**Criterio de KC-CM-11-4 (vigencia > 99 % tras el refresco diario).** Lo comprueba
`test_tras_el_refresco_diario_la_vigencia_supera_el_99_por_ciento`. Ejecuta un refresco incremental real contra
PostgreSQL con estas condiciones:

- cambia un fichero;
- fuera de él hay seis símbolos y dos definiciones curados, que dejan la vigencia en el 93 %;
- el refresco solo reembebe el fichero cambiado y el paso de vigencia repara el resto.

La vigencia final es del 100 %, y el hash de cada embedding coincide con el texto canónico recalculado desde la
descripción en base.

## 2. Índices HNSW bajo rotación diaria

### 2.1 Qué se midió

`bench_hnsw_rotacion.py` simula el refresco con estas condiciones:

- **Corpus:** embeddings BGE-M3 reales ampliados (el mismo corpus semi-real de ADR-022) a 20.000 filas.
- **Rotación:** 7 días con un 5 % de reembebido diario, aplicado con COPY + UPSERT igual que `GraphLoader`.
- **Recall:** recall@10 frente al top-10 **exacto** de cada día, con `ef_search = 100` y sin escaneo iterativo.

Con `ef_search = 400`, el valor de Diseño, el recall se satura y oculta la degradación.

**Corpus de definiciones** (`docs/benchmarks/hnsw_rotacion_definiciones_v1.json`):

| Configuración | Mantenimiento | recall@10 día 0 | día 7 | mínimo | índice día 0 → 7 | hinchazón | VACUUM/día | REINDEX | recall tras REINDEX |
|---|---|---|---|---|---|---|---|---|---|
| m16 ef64 (anterior) | sin VACUUM | 0,993 | 0,979 | 0,973 | 156,3 → 210,9 MB | **+35,0 %** | — | 14,5 s | 0,990 |
| m16 ef64 (anterior) | VACUUM diario | 0,995 | 0,964 | **0,964** | 156,3 → 164,1 MB | +5,0 % | 16,6 s | 13,1 s | 0,991 |
| m16 ef128 | VACUUM diario | 0,996 | 0,988 | 0,987 | 156,3 → 164,1 MB | +5,0 % | 29,1 s | 21,7 s | 1,000 |
| **m24 ef128 (elegida)** | VACUUM diario | 0,990 | 0,991 | **0,989** | 156,3 → 164,1 MB | +5,0 % | 45,3 s | 32,2 s | 0,996 |
| halfvec m16 ef64 | VACUUM diario | 0,986 | 0,966 | 0,956 | 52,1 → 54,7 MB | +5,0 % | 11,0 s | 12,1 s | 0,979 |

**Corpus de documentación** (`docs/benchmarks/hnsw_rotacion_docs_v1.json`):

| Configuración | Mantenimiento | recall@10 día 0 | día 7 | mínimo | hinchazón | VACUUM/día | REINDEX | recall tras REINDEX |
|---|---|---|---|---|---|---|---|---|
| m16 ef64 (anterior) | sin VACUUM | 0,987 | 0,969 | 0,969 | +35,0 % | — | 10,6 s | 0,984 |
| m16 ef64 (anterior) | VACUUM diario | 0,990 | 0,944 | **0,944** | +5,0 % | 12,6 s | 10,6 s | 0,990 |
| **m24 ef128 (elegida)** | VACUUM diario | 0,995 | 0,980 | **0,976** | +5,0 % | 29,5 s | 26,1 s | 0,990 |

### 2.2 Qué se concluye

1. **Sin VACUUM, el índice crece +35 % en una semana**, y crece con la rotación acumulada. Cada UPDATE deja el
   vector viejo en el grafo, y solo el REINDEX lo compacta. **El VACUUM diario lo contiene en +5 %**, porque
   reutiliza el espacio de los nodos que desconecta.
2. **Con los parámetros anteriores, el VACUUM degrada el recall.** pgvector repara los vecinos de cada nodo
   eliminado con una búsqueda de `ef_construction` candidatos. Con 16/64 esas reparaciones empeoran el grafo y el
   recall@10 cae 3 puntos en definiciones y 4,6 en documentación. Sin VACUUM cae menos, porque los nodos muertos
   siguen sirviendo de paso, pero el índice se hincha.
3. **Con m = 24 y ef_construction = 128 el recall no baja** (criterio de KC-CM-11-4):
   - en definiciones se mantiene plano durante toda la semana (0,989–0,991);
   - en documentación cae de 0,990 a 0,976 y el REINDEX semanal lo devuelve a 0,990, frente a 0,944 con la
     configuración anterior.
4. **El tamaño no cambia con `m`.** Con 1024 dimensiones en float4 cada elemento ocupa una página de 8 KB: son
   156,3 MB por cada 20.000 filas en cualquier configuración float4.
5. **El coste es de tiempo de mantenimiento:** el VACUUM diario pasa de unos 17 s a unos 45 s y el REINDEX de
   unos 13 s a unos 32 s por cada 20.000 filas con un 5 % de rotación.
6. **halfvec sigue sin cumplir el criterio de ADR-022** (apéndice del ADR): bajo rotación pierde 3,3 puntos frente
   a la configuración elegida.

### 2.3 Cómo se aplican los parámetros sin bloquear

- **`vec.hnsw_catalogo`** pasa a `m = 24, ef_construction = 128` en `definition_embeddings_hnsw_ix` y
  `doc_chunks_hnsw_ix`. `ticket_embeddings_hnsw_ix` no cambia: lo escribe el backend ticket a ticket, no rota a
  diario.
- **El índice padre no se puede cambiar.** PostgreSQL **no admite** `ALTER INDEX ... SET` sobre el índice de una
  tabla particionada, pero sí sobre el de cada partición, y `REINDEX` conserva las opciones del índice.
- **Particiones existentes:** `vec.alinear_parametros_hnsw()` pone en cada índice hoja los parámetros del catálogo.
  Es un cambio de metadatos instantáneo, con `lock_timeout` de 5 s y reintento si el índice está ocupado. La
  migración lo ejecuta una vez.
- **Reconstrucción del grafo:** se hace con los nuevos parámetros en el **siguiente REINDEX semanal** (044, domingo
  03:15 / 04:15 UTC). Para no esperar, se puede lanzar
  `REINDEX INDEX CONCURRENTLY vec.definition_embeddings_hnsw_ix;` como `kallicode_admin`.
- **Particiones nuevas:** una partición creada después hereda las opciones del padre (16/64). El trabajo diario
  `hnsw_alinear_parametros` la corrige.
- **Construcción desde cero:** `db/06_indices_concurrentes.sql` ya lee los parámetros del catálogo.

## 3. Rutina de mantenimiento (pg_cron)

Acordada con Database: el VACUUM diario se suma al REINDEX semanal de KC-DB-08-2, en una ventana que no se pisa con
la del domingo (02:00–06:00).

| Trabajo | Cuándo (UTC) | Orden | Coste medido (20.000 filas, 5 % diario) |
|---|---|---|---|
| `hnsw_alinear_parametros` | diario 00:50 | `SELECT * FROM vec.alinear_parametros_hnsw()` | instantáneo (solo metadatos) |
| `hnsw_vacuum_definition_embeddings` | diario 01:00 | `VACUUM (ANALYZE) vec.definition_embeddings` | 45 s |
| `hnsw_vacuum_doc_chunks` | diario 01:30 | `VACUUM (ANALYZE) vec.doc_chunks` | 30 s |
| REINDEX semanal (044) | dom 02:15 – 06:00 | `REINDEX INDEX CONCURRENTLY ...` | 26–32 s por índice |

**Proyección de la ventana.** El coste del VACUUM es proporcional a las filas que rotan, porque repara los vecinos
de cada nodo eliminado. Con 200.000 nodos y un 5 % diario (10.000 reembebidos) se esperan unos 8 minutos por
colección. La ventana de 30 minutos entre trabajos cubre hasta unos 750.000 filas por colección.

**Cómo confirmarlo en producción.** El tiempo real de cada trabajo queda en `cron.job_run_details`:

```sql
SELECT j.jobname, r.start_time, r.end_time - r.start_time AS duracion, r.status
  FROM cron.job_run_details r JOIN cron.job j USING (jobid)
 WHERE j.jobname LIKE 'hnsw_%' ORDER BY r.start_time DESC LIMIT 20;
```

Si el refresco diario de un tenant cae dentro de la ventana, no hay conflicto de corrección: VACUUM no bloquea
escrituras. Sin `pg_cron` instalado la migración solo avisa; ejecute esas órdenes desde el planificador que tenga
(`docs/runbook-vectorial.md`, sección 4).

## 4. Métricas (worker de CodeMapping, `/metrics`)

El worker lee `vec.estado_hnsw()` y `vec.vigencia_global()` cada `CM_WORKER_METRICAS_VECTORIALES_CADA_S` segundos
(300 por defecto; 0 = nunca). Las dos funciones son SECURITY DEFINER, porque `vec.hnsw_*` es solo de
`kallicode_admin`, y no devuelven ningún dato de un tenant. Ninguna métrica lleva `tenant_id`.

| Métrica | Etiquetas | Qué mide |
|---|---|---|
| `kc_codemapping_hnsw_tamano_bytes` | `indice` | Tamaño del HNSW (suma de sus particiones) |
| `kc_codemapping_hnsw_hinchazon_porcentaje` | `indice` | Bytes por fila viva frente a la línea base tras construcción o REINDEX (sin línea base no se publica) |
| `kc_codemapping_hnsw_tuplas_muertas_porcentaje` | `indice` | Tuplas muertas pendientes de VACUUM |
| `kc_codemapping_hnsw_segundos_desde_vacuum` | `indice` | Antigüedad del último VACUUM (manual o automático) |
| `kc_codemapping_embeddings_vigencia_ratio` | `capa` | Vigencia de todos los tenants |
| `kc_codemapping_embeddings_no_vigentes` | `capa`, `estado` | Nodos obsoletos o sin embedding |
| `kc_codemapping_embeddings_tenants_bajo_umbral` | `capa` | Tenants por debajo del 99 % |
| `kc_codemapping_embeddings_reembebidos_total` | `motivo` | Reembebidos por el paso de vigencia |
| `kc_codemapping_embeddings_vigencia_tras_refresco` | — | Histograma de la vigencia al terminar cada refresco |

Alertas sugeridas:

- `embeddings_tenants_bajo_umbral > 0` durante más de 24 h: la reparación no llega; revisar TEI o subir
  `CM_VIGENCIA_MAX_REEMBEBIDOS`.
- `hnsw_segundos_desde_vacuum > 2 días`: la rutina no corre.
- `hnsw_hinchazon_porcentaje > 30`: adelantar el REINDEX (runbook, sección 4.3).

## 5. Límites conocidos

- **El corpus del benchmark es semi-real** (ADR-022): la distribución del modelo es la real, pero los vecinos se
  sintetizan. Las cifras orientan; la confirmación con datos de cliente es la medición nocturna y
  `cron.job_run_details`.
- **Recall de documentación:** con la configuración elegida, la documentación aún pierde 1,5 puntos de recall@10
  a lo largo de la semana. El REINDEX semanal lo recupera. Si en producción la pérdida fuera mayor, el siguiente
  paso es subir `ef_construction` o adelantar el REINDEX de `doc_chunks`.
- **Opciones del índice padre:** el índice padre de las tablas particionadas conserva las opciones 16/64 en el
  catálogo del sistema. No tiene almacenamiento, pero es la plantilla de las particiones nuevas, que se corrigen
  en la alineación diaria; hasta su primer REINDEX su grafo se construye con 16/64. Cambiar el padre exige
  reconstruir el índice entero con `db/06_indices_concurrentes.sql`.
- **Alcance de la vigencia:** se mide para las capas que escribe el pipeline en `vec.definition_embeddings`
  (`symbol` y `definition`). `vec.doc_chunks` ya se reutiliza y purga por `contenido_hash` (KC-CM-08-5).
