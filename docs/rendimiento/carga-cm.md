# Carga y resiliencia de la capa de consulta de CodeMapping (KC-CM-11-5)

Informe para Testing y para el expediente de release. Cifras medidas el 16 de noviembre de 2026.

## Resumen

| Criterio | Resultado |
|---|---|
| 50 consultas concurrentes con el p95 de cada operación dentro de su presupuesto (k6) | **No se cumple todavía.** Con 4 workers, 6 de 10 operaciones cumplen. `impacto` y `dependencias` dan un p95 de 235-242 ms frente a 120 ms; `resolver_nuggets` y `resolver_incidentes`, 243-255 ms frente a 200 ms. Sin concurrencia, las diez están muy por debajo: `impacto` da 15 ms de p95 sobre 200.000 nodos. La diferencia es cola por falta de capacidad, no coste de las consultas (ver [Diagnóstico](#diagnóstico)). |
| Caída de TEI, del LLM o de la base → degradación controlada con errores tipificados, sin 500 sin catalogar | **Se cumple.** PostgreSQL se detuvo 20 s con 50 agentes en carga: 0 respuestas sin catalogar, 784 respuestas 503 `DEPENDENCIA_NO_DISPONIBLE` con `Retry-After`, y la API vuelve sola. TEI y el LLM están cubiertos por pruebas automáticas. |
| La ingesta interrumpida por una caída de la base reanuda desde su checkpoint sin duplicar coste | **Se cumple.** El reintento no vuelve a llamar al LLM ni a reservar presupuesto. La prueba falla con el código anterior y pasa con el nuevo. |
| Informe entregado a Testing e incorporado al expediente de release | Este documento. Faltan la entrega a Testing y la incorporación al expediente, que es de KC-DB-12-5. |

## Qué se mide y cómo

**Superficie.** La API interna expone las diez operaciones del contrato cm-grafo-v1, una ruta por
operación: `POST /internal/v1/codemapping/grafo/{resolver-nodos, semillas, impacto, dependencias,
subgrafo, vecinos, dependencias-ocultas, busqueda, nuggets, incidentes}`. Usa el JWT de servicio y
la sesión RLS del tenant. La implementación es `kallicode_core.cm_grafo_sesion.ConsultaGrafoSesion`:
la misma SQL que usa el cliente de los agentes (`ConsultaGrafoPostgres`). Una prueba compara las
respuestas de los dos clientes sobre el mismo grafo.

**Datos.** El perfil `proyeccion_200k` de `packages/core/tests/bench_cm_khop.py` (KC-DB-11-3) tiene:
- 200.000 nodos y 300.000 aristas;
- grado de entrada con cola pesada: un 5 % de las semillas son nodos centrales, con un grado de entrada máximo de 1.905;
- `cm.reachability` vigente, con 938.330 filas;
- además, 20.000 embeddings de símbolos y 2.000 incidentes enlazados al grafo.

Las reglas (nuggets) no se siembran porque se enlazan a través de la documentación: la consulta recorre el grafo y devuelve pocas reglas.

**Carga.** `testing/carga/cm_consulta.js` lanza 50 usuarios virtuales concurrentes, sin pausa entre peticiones. La mezcla de operaciones es la de un agente antes de actuar:

| Operación | Peso |
|---|---:|
| `impacto` | 25 % |
| `dependencias` | 15 % |
| `semillas`, `resolver_nodos`, `subgrafo`, `vecinos` | 10 % cada una |
| `dependencias_ocultas`, `buscar_hibrido`, `resolver_nuggets`, `resolver_incidentes` | 5 % cada una |

El presupuesto p95 de cada operación sale del contrato (`kallicode_core.cm_grafo.OPERACIONES`), con una excepción: `impacto` y `dependencias` tienen 120 ms según ADR-020. `testing/carga/carga_cm.py` genera `datos.json` desde esas fuentes, así que el script no contiene ningún número escrito a mano.

**Entorno de esta medición.**
- API en Linux con uvicorn y uvloop, como en `apps/api/Dockerfile`.
- PostgreSQL 16 con pgvector y pg_partman (imagen `db/postgres`).
- Redis y k6 0.54.0 en la misma red de Docker.
- Todo comparte un portátil de 12 núcleos. No es capacidad dedicada: la cifra válida para producción será la del job nocturno `carga_cm_nocturno.yml`, que corre en un runner limpio.
- Las mediciones en Windows nativo se descartaron: el bucle Proactor sin uvloop y el reparto de conexiones entre workers no representan el despliegue.

## Resultados

### Tiempo de servicio (1 agente, sin cola)

| Operación | p50 (ms) | p95 (ms) | p99 (ms) | Presupuesto p95 |
|---|---:|---:|---:|---:|
| `resolver_nodos` | 5,4 | 6,6 | 7,2 | 500 |
| `semillas` | 6,3 | 7,8 | 8,7 | 500 |
| `impacto` | 7,2 | 15,3 | 29,0 | 120 |
| `dependencias` | 7,2 | 9,0 | 10,1 | 120 |
| `subgrafo` | 8,6 | 90,8 | 246,9 | 500 |
| `vecinos` | 5,6 | 8,5 | 20,4 | 500 |
| `dependencias_ocultas` | 8,3 | 12,8 | 27,9 | 500 |
| `buscar_hibrido` | 14,6 | 20,4 | 58,7 | 400 |
| `resolver_nuggets` | 8,1 | 9,7 | 11,3 | 200 |
| `resolver_incidentes` | 9,0 | 19,8 | 36,7 | 200 |

Son tiempos de extremo a extremo por HTTP: incluyen autenticación, sesión RLS, consulta y serialización.

### 50 agentes concurrentes, 3 minutos, 4 workers

75.734 peticiones en 180 s (420,6 req/s), sin errores.

| Operación | Peticiones | p50 (ms) | p95 (ms) | p99 (ms) | Presupuesto p95 | Cumple |
|---|---:|---:|---:|---:|---:|---|
| `resolver_nodos` | 7.394 | 77,7 | 226,3 | 344,5 | 500 | sí |
| `semillas` | 7.703 | 80,0 | 221,7 | 341,6 | 500 | sí |
| `impacto` | 18.901 | 87,0 | 241,5 | 363,4 | 120 | **no** |
| `dependencias` | 11.368 | 86,5 | 234,5 | 359,9 | 120 | **no** |
| `subgrafo` | 7.536 | 98,8 | 321,3 | 462,5 | 500 | sí |
| `vecinos` | 7.575 | 78,8 | 227,7 | 338,7 | 500 | sí |
| `dependencias_ocultas` | 3.777 | 93,3 | 253,5 | 369,4 | 500 | sí |
| `buscar_hibrido` | 3.714 | 95,3 | 259,1 | 370,8 | 400 | sí |
| `resolver_nuggets` | 3.874 | 85,4 | 243,3 | 352,0 | 200 | **no** |
| `resolver_incidentes` | 3.892 | 91,0 | 254,8 | 373,0 | 200 | **no** |

### Escalado con el número de workers (50 agentes, 60 s)

| Workers | req/s | p50 de `impacto` (ms) | p95 de `impacto` (ms) |
|---:|---:|---:|---:|
| 1 | 156 | 295 | 459 |
| 4 | 411 | 76 | 286 |
| 8 | 520 | 52 | 362 |

Con 8 workers el p50 sigue bajando, pero el p95 empeora, y lo hace igual en todas las operaciones: la máquina ya no tiene núcleos libres para PostgreSQL, k6 y la API a la vez.

## Diagnóstico

- **La latencia con carga es cola, no coste de la consulta.**
  - Sin concurrencia, `impacto` tarda 7 ms de mediana.
  - Con 50 agentes, todas las operaciones tienen la misma cola, también `resolver_nodos`, que en la base cuesta menos de 1 ms.
  - Por la ley de Little, 50 peticiones en curso a 420 req/s dan ~120 ms de media por petición, que es lo que se observa.
  - **Conclusión:** el p95 depende de la capacidad que se despliegue, no del grafo.
- **Capacidad por worker:** unas 155 req/s por proceso de uvicorn. Para que el p95 de `impacto` baje de 120 ms con 50 agentes, la media tiene que rondar los 40 ms, es decir, unas 1.200 req/s de capacidad dedicada. Con el coste actual por petición son unos 8 workers con núcleo propio, por ejemplo 3 réplicas de 3 workers, sin contar PostgreSQL. Es una estimación: el job nocturno medirá la cifra real.
- **Sobrecoste por petición que se puede recortar** (afecta a toda la API, así que queda fuera de este ticket):
  - Cada petición hace 8 viajes a la base: pre-ping del pool, BEGIN, dos `set_config`, `statement_timeout`, la consulta y COMMIT.
  - Juntar los dos `set_config` en una sentencia, fijar el `statement_timeout` en el mismo paso y revisar `pool_pre_ping` reduciría ese número casi a la mitad.
  - El pool de la API admite 30 conexiones por proceso (`pool_size` 10 + `max_overflow` 20).

## Resiliencia

### Matriz de caídas

| Dependencia caída | Consultas del grafo | Búsqueda por texto | `{{incidents}}` | Ingesta | Evidencia |
|---|---|---|---|---|---|
| PostgreSQL (conexión rechazada, servidor reiniciado, failover) | 503 `DEPENDENCIA_NO_DISPONIBLE`, `detalle.servicio = "base de datos"`, `Retry-After: 5`. El cliente se recupera solo cuando vuelve la base. | Igual | Igual | Falla y el snapshot queda `fallido`. El reintento reanuda desde el checkpoint. | `test_base_caida_*` (API y agentes), caos con k6 |
| PostgreSQL desaparece sin cerrar los sockets | 503 en ≤ 5 s (`KC_DB_CONNECT_TIMEOUT_S`) o ≤ 15 s por sentencia (`KC_DB_COMMAND_TIMEOUT_S`), no una petición colgada | Igual | Igual | — | Caos con k6 (máximo observado: 5,2 s); `test_una_sentencia_que_supera_command_timeout_es_una_caida` |
| TEI (conexión, timeout, 5xx, 429) | Siguen respondiendo: no dependen de TEI | 503 `detalle.servicio = "embeddings"` | Se resuelve sin orden semántico | — | `test_tei_caido_degrada_sin_tumbar_el_grafo` |
| Proveedor LLM (tiers agotados) | Siguen respondiendo con el grafo publicado | — | — | `EscaladoSemantico`, que el worker escala a una persona (KC-CM-10-6). Snapshot `fallido`. | `test_llm_caido_falla_la_ingesta_tipificada_y_el_grafo_sigue_sirviendo` |

Un error que no es una caída (SQL inválido, 400 de TEI, `ConnectionError` ajeno a la base) no se
disfraza de 503: sale con su propio error, y hay pruebas que lo comprueban.

### Caos con carga real

50 agentes durante 90 s. PostgreSQL se detiene con `docker stop` a los 30 s y arranca 20 s después. Con 4 workers:

| Medida | Valor |
|---|---|
| Peticiones | 26.040 |
| Respuestas sin catalogar | **0** |
| 503 `DEPENDENCIA_NO_DISPONIBLE` | 784 |
| Tasa de error total | 3,9 % (todas durante la caída) |
| Latencia máxima | 5,2 s (la cota de conexión) |
| Consultas de impacto correctas después, sin reiniciar la API | 20/20 |

La primera ejecución del caos encontró 50 peticiones colgadas hasta el timeout del cliente, una por agente: eran las que estaban en curso cuando desapareció el servidor. Asyncpg esperaba sin límite. Se resolvió acotando el motor de la API (`KC_DB_CONNECT_TIMEOUT_S=5`, `KC_DB_COMMAND_TIMEOUT_S=15`). Los motores admin y worker no cambian, porque ejecutan tareas largas legítimas.

### Reanudación de la ingesta sin duplicar coste

`build_graph.full_build` guarda en el checkpoint local del snapshot la salida del LLM:
- `semantica_llm`: descripciones y definiciones;
- `ontologia`: ontología, enlaces y cola de curación.

Antes solo se guardaban las fases 1-3. `test_ingesta_interrumpida_por_la_base_reanuda_sin_repetir_el_llm` corta de verdad la conexión de la ingesta con `pg_terminate_backend` en dos puntos: después del LLM (cola de curación) y en la persistencia final (vectores). En ambos casos el reintento:
- reutiliza el mismo snapshot y lo deja `completo`;
- hace 0 llamadas nuevas al LLM: las 5 del primer intento no se repiten;
- no vuelve a hacer la estimación en seco del presupuesto.

Con el código anterior la prueba falla con «el reintento volvió a llamar al LLM».

Los embeddings sí se recalculan, porque TEI es autoalojado y no cobra por token.

## Hallazgos durante la prueba

1. **Plan degenerado de `dependencias_ocultas` con tenants de tamaños muy distintos.** Corregido en este ticket.
   - Con el tenant de 200.000 nodos en la base, la consulta tardaba 33 s para un tenant de 4.350 nodos: el planificador estimaba 1 fila en `cm.nodes` y repetía el anti-join por cada nodo.
   - `acoplados` pasa a `MATERIALIZED` y la consulta baja a 0,2 s. El resultado no cambia, como comprueban las pruebas de paridad y de contrato.
   - Conviene que Database revise si otras consultas del ADR-020 tienen el mismo riesgo.
2. **`apps/api/Dockerfile` no puede arrancar la API interna.** No se corrige aquí.
   - No copia `apps/workers`, y `routers/internal.py` importa `kallicode_workers`.
   - `uv sync` sin `--all-packages` no instala los paquetes del workspace (`kallicode_api`, `kallicode_core`) ni sus dependencias (`azure.storage`).
   - Además, algo importa `azure.keyvault` sin declararlo en el paquete de la API.
   - La medición usó una imagen propia con `uv sync --all-packages`.
3. **`budget.py` (KC-CM-05-5) falla contra cualquier base real.** No se corrige aquí. La estimación en seco consulta `core.ciclo_inicio(text, timestamptz)`, que no existe en ninguna migración, y la columna `core.subscriptions.limite_tokens_mensual`, que tampoco existe. El benchmark de KC-CM-11-1 ya lo esquivaba con un parche.

## Cómo reproducirlo

```bash
# PostgreSQL con el esquema aplicado, Redis y las variables KC_DATABASE_URL* y KC_REDIS_URL.
uv run python testing/carga/carga_cm.py preparar                 # ~3 min: grafo de 200.000 nodos
uv run python testing/carga/carga_cm.py ejecutar --workers 4     # carga, 3 min
uv run python testing/carga/carga_cm.py ejecutar --workers 4 --caos --contenedor-pg <contenedor> --duracion 90s
uv run pytest packages/codemapping/tests/test_resiliencia.py apps/api/tests/test_codemapping_grafo.py \
  packages/core/tests/test_caida_dependencias.py
```

El orquestador usa el binario `k6` si está en el PATH y, si no, la imagen `grafana/k6:0.54.0`. Deja el resumen de k6 (`resumen_*.json`) y el informe Markdown (`informe_*.md`) en `--salida`.

El job `.github/workflows/carga_cm_nocturno.yml` ejecuta las dos fases cada noche y publica los resultados como artefacto. Por ahora no corre en los PR: con 4 workers el umbral de p95 todavía no se cumple, y bloquearía cualquier cambio de la API hasta que se decida la capacidad de despliegue.
