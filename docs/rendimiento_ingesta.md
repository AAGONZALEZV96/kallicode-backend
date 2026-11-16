# Rendimiento de la ingesta del grafo a 200.000 nodos (KC-CM-11-1)

Benchmark `packages/codemapping/tests/bench_ingesta_escala.py` · generador
`packages/codemapping/src/kallicode_codemapping/repo_sintetico.py` · migración
`db/migrations/062_cm_analizar_por_fase.sql` · CI `.github/workflows/ingesta_escala_nocturno.yml`.

## 1. Qué se mide

Un repositorio git sintético y determinista de **200.000 nodos y 260.000 aristas** se ingesta con el
pipeline real (`build_graph.full_build`: recorrido, parseo AST en pool de procesos, checkpoint, carga
COPY, capa semántica) y después se modifican 200 archivos y se ejecuta `refresh.run_refresh` (el refresco
diario). Se ejecuta como `kallicode_app` con RLS real.

| Capa | Nodos | Arista | Aristas |
|---|---:|---|---:|
| files | 8.000 | CALLS | 214.000 |
| symbols | 176.000 | USES_TABLE | 24.000 |
| db_tables | 600 | REFERENCES | 1.200 |
| db_columns | 9.000 | REALIZED_BY | 12.000 |
| definitions | 6.000 | EXPRESSES | 6.000 |
| concepts | 400 | RELATES | 2.800 |
| **Total** | **200.000** | **Total** | **260.000** |

Los recuentos esperados salen de la aritmética del generador, no de lo que la carga escribió; la prueba
compara capa por capa, y arista por arista, tras la ingesta y tras el refresco (cada archivo modificado
gana un símbolo y una llamada).

**Dobles**: solo LLM, embeddings TEI, extracción del esquema de la base del cliente, la verificación del
presupuesto de tokens y `upsert_vectors` (índices HNSW: KC-CM-08-4 y KC-CM-11-4). Todo lo demás es real.

## 2. Resultado (escala completa, Postgres 16 local, 2 CPU)

| Medida | Resultado | Presupuesto (puerta del CI) |
|---|---:|---:|
| Ingesta completa de 200.000 nodos / 260.000 aristas | 0,4 min | < 90 min |
| RSS máximo de la ingesta (proceso + pool) | 485 MiB | < 3 GiB |
| Refresco diario de 200 archivos | 0,23 min | < 5 min |
| Recuentos por capa frente al generador | idénticos | idénticos |

Fases de la ingesta (s): recorrido 0,2 · parseo AST 5,7 · checkpoint 0,3 · archivos 0,4 · esquema 0,9 ·
**símbolos 11,9** (la mayor, incluye las 214.000 aristas CALLS) · semántica 0,9. En el refresco, 12,4 de los
14 s son `cm.refrescar_reachability()`.

El benchmark nocturno vuelve a medir esto con los presupuestos como pruebas: si algún día la ingesta
tarda 90 min, pasa de 3 GiB o el refresco de 5 min, el job falla. El informe por fase y el plan de índices
quedan en el resumen del job y como artefacto (`ingesta-escala`). Los números de este documento son de un
Postgres local sin latencia de red; la referencia vigente es la del job nocturno.

## 3. Qué hizo falta para llegar aquí

- **ANALYZE por fase (`cm.analizar_tablas`, migración 062).** Tras una carga masiva las tablas no tienen
  estadísticas hasta que el autovacuum pasa (minutos). Medido con 20.000 nodos, `cm.refrescar_reachability()`
  tarda 1,5 s con estadísticas y **63 s sin ellas** (x42). `kallicode_app` no es dueño de `cm.*` y en
  PostgreSQL 16 solo el dueño puede hacer ANALYZE, por eso la función `SECURITY DEFINER`, con lista blanca de
  tablas. El cargador (`GraphLoader`) analiza al cerrar cada fase que escribió 5.000 filas o más, y la tabla
  temporal de staging antes de insertar desde ella. Si la función no existe, avisa y sigue: nunca tumba la ingesta.
  Un refresco diario de unos cientos de archivos queda bajo el umbral y lo cubre el autovacuum.
- **COPY por lotes.** La carga ya usa `COPY` a una tabla temporal más `INSERT ... ON CONFLICT`, una transacción
  por fase. A 200.000 nodos el RSS pico es 485 MiB (6,5 veces bajo el presupuesto), así que no se partió en
  lotes; el parseo en flujo de S4 es lo que acota la memoria.
- **Cuatro defectos contra una base real**, que las pruebas con mocks no veían: `SET LOCAL app.tenant_id = %s`
  no admite parámetros con psycopg 3 (ahora `set_config`); `SnapshotManager` usaba los estados `iniciado` y
  `vigente`, que el CHECK `snapshots_estado_chk` rechaza (ahora `en_progreso` y `completo`); le faltaba
  `obtener_checkpoint`; y no normalizaba el DSN `postgresql+psycopg://`.

## 4. Índices diferidos: por qué no se aplican en producción

El ticket pide índices diferidos durante la carga inicial. Se evaluó y **no se incorpora al camino de
producción**: `cm.*` son tablas compartidas por todos los tenants. Quitar sus índices (PK incluida, de la que
depende `ON CONFLICT`) o dejarlos inválidos durante la carga de un tenant degradaría las consultas y la
integridad de los demás, y la reconstrucción es sobre la tabla entera, no sobre el tenant. La carga ya cabe
con holgura sin ello. Queda como palanca de recuperación de desastre (KC-DB-11-4): reconstruir un cm vacío con
los índices creados después.

## 5. Plan de índices (para Database)

Tamaños tras cargar 200.000 nodos y 260.000 aristas más el refresco:

| Tabla | Índice | MiB | Lecturas (idx_scan) | Clase |
|---|---|---:|---:|---|
| edges | edges_out_ix | 49,5 | 10 | secundario |
| edges | edges_in_ix | 38,5 | 9 | secundario |
| edges | edges_pkey | 32,1 | 386.742 | PK |
| edges | edges_snapshot_idx | 2,8 | 27 | secundario |
| symbols | idx_symbols_tenant_file_id | 26,4 | 3.460 | secundario |
| symbols | symbols_pkey | 17,0 | 264.115 | PK |
| files | idx_files_tenant_path_sha | 2,5 | 0 | secundario |
| files | files_tenant_id_path_key | 0,8 | 0 | único |
| files | files_pkey | 0,8 | 267.480 | PK |
| files | files_snapshot_idx | 0,1 | 40.488 | secundario |
| db_columns | db_columns_tenant_id_table_id_nombre_key | 0,8 | 274 | único |
| db_columns | db_columns_pkey | 0,6 | 12.960 | PK |

Observaciones para Database:

1. **`edges` pesa 123 MiB de índices frente a 28 MiB de datos.** `edges_out_ix` y `edges_in_ix` son los que
   usan los recorridos k-hop; tienen pocas lecturas aquí porque el benchmark no consulta el grafo. No se
   deben retirar: su uso lo mide KC-DB-11-3 (consultas del ADR-020), no este benchmark.
2. `idx_files_tenant_path_sha` (2,5 MiB, 0 lecturas) es candidato a revisión: `files_tenant_id_path_key` ya
   cubre `(tenant_id, path)`. Solo con evidencia de la consulta real; no se tocó (`docs/plan-indices.md`).
3. El tamaño de índices crece lineal con las aristas; a 260.000 aristas no hay un índice dominante nuevo.
4. La migración 062 no crea índices.

## 6. Cómo ejecutarlo

```bash
# Local, con KC_DATABASE_URL_MIGRATIONS apuntando a un Postgres con las migraciones aplicadas
KC_BENCH_ING_DIVISOR=10 uv run pytest packages/codemapping/tests/bench_ingesta_escala.py -v   # 1/10
KC_BENCH_ING_DIVISOR=1  KC_BENCH_RESULTADOS=informe.md uv run python packages/codemapping/tests/bench_ingesta_escala.py
```

Variables: `KC_BENCH_ING_DIVISOR` (1 = completa), `KC_BENCH_ING_MODIFICADOS` (200), `KC_BENCH_RESULTADOS`
(ruta del `.md`; escribe también el `.json`), `KC_BENCH_CONSERVAR` (no borra los datos al terminar).
