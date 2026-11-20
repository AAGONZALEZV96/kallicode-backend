# Runbook: reconstrucción verificada del grafo `cm` de un tenant (KC-CM-12-1)

Para quien deba recuperar el grafo de código de **un tenant** y no conozca el sistema. Se puede seguir de arriba abajo sin
ayuda del equipo de desarrollo. Complementa el PITR de [recuperacion_pitr.md](recuperacion_pitr.md) (KC-DB-11-1) y la
política por tabla de [recuperacion_cm_hnsw.md](recuperacion_cm_hnsw.md) (KC-DB-11-4). Última ejecución guardada del
ensayo: [ensayo_reconstruccion_grafo_acta.md](ensayo_reconstruccion_grafo_acta.md).

> **Estado de la revisión.** Pendiente de la revisión de Database (criterio de aceptación) y de una prueba de lectura
> por una persona ajena al equipo. Registrar ambas en la tabla del final.

## 0. Qué es y cuándo usarlo

El grafo se **reconstruye**, no se exporta: sale de volver a leer el repositorio con parser determinista.

| Situación | Camino |
|---|---|
| Se perdió o corrompió el grafo de UN tenant; la capa semántica (definiciones, conceptos) está bien | **A. `determinista`** (§3) |
| Se perdió también la capa semántica y no hay copia de seguridad que la restaure | **B. `completo`** (§4). Cuesta LLM y horas |
| Se perdió la base entera, o hay que volver a un instante concreto | **PITR** ([recuperacion_pitr.md](recuperacion_pitr.md)), no este runbook |
| Solo faltan o están obsoletos embeddings | §5, sin tocar el grafo |

## 1. Objetivos (RPO / RTO)

Registrados en `core.objetivos_recuperacion` y consultables con:

```sql
SELECT * FROM core.objetivos_recuperacion;
```

| Objetivo | Valor propuesto | Estado |
|---|---|---|
| RPO del esquema `cm` | 5 min (el de la base, por WAL) | **propuesto**, pendiente de acuerdo con Database |
| RTO de la reconstrucción de un tenant | 60 min | **propuesto**, pendiente de acuerdo con Database |

El **RPO** de la reconstrucción determinista es cero para lo que sale del repositorio (se vuelve a leer el HEAD) y el de
la copia de seguridad para lo demás. El **RTO** se cuenta desde que se ejecuta el borrado o desde que se decide
reconstruir (el origen está sano y el repositorio accesible) hasta que `cm.recuentos_grafo` coincide con la
referencia; **no** incluye detectar el desastre ni decidir. Los valores medidos están en el acta del ensayo.

## 2. Antes de empezar (prerrequisitos)

1. Datos del tenant: su identificador (`org_xxx`), la URL o ruta del repositorio y, si lo hay, la URL de la base del cliente.
2. Acceso: rol `kallicode_admin` para borrar (§3.2); el servicio de ingesta con las variables `CM_*` del tenant
   (`CM_TENANT_ID`, `CM_REPO_URL`, `CM_DATABASE_URL` si aplica, `CM_STORE_DATABASE_URL`).
3. **Anotar los recuentos de partida**, si el grafo aún existe (§3.1). Si ya no existe, se compara con el último acta
   o con el último ensayo mensual.
4. Avisar a quien use el tenant: **mientras se reconstruye, el tenant no tiene grafo** (el borrado es en el sitio).
5. Tener la última copia de seguridad accesible por si hay que restaurar la capa semántica o los vectores.

## 3. Camino A: reconstrucción determinista

### 3.1 Recuentos de partida

```sql
SELECT * FROM cm.recuentos_grafo('org_xxx') ORDER BY objeto;
```

Guardar el resultado (objeto → cantidad). Aparecen `nodo:<kind>`, `arista:<tipo>` y `reachability`.

### 3.2 Borrar la capa determinista (solo si hace falta; si el grafo ya se perdió, saltar)

Como `kallicode_admin`. La función exige repetir el tenant como confirmación:

```sql
SELECT * FROM cm.vaciar_grafo_tenant('org_xxx', 'determinista', 'org_xxx');
```

### 3.3 Reconstruir

```bash
export CM_TENANT_ID=org_xxx CM_REPO_URL=<url-o-ruta> CM_STORE_DATABASE_URL=<url>
uv run python -m kallicode_codemapping.build_graph --full --alcance determinista
```

Esta modalidad **no llama al LLM ni a TEI**: relee el repositorio, carga archivos, símbolos, aristas y esquema de la
base del cliente, calcula el cierre transitivo (`cm.reachability`) y deja el snapshot en estado `completo`. El log
termina con `reconstrucción determinista completa`. Si el proceso se interrumpe, se puede **volver a lanzar el mismo
comando**: reanuda desde el último checkpoint.

### 3.4 Validar (obligatorio; sin esto no se da por recuperado)

```sql
SELECT * FROM cm.recuentos_grafo('org_xxx') ORDER BY objeto;
```

Criterio: **cada** `nodo:*`, `arista:*` y `reachability` debe estar dentro del **0,5 %** de los recuentos de partida
(en el ensayo salen idénticos). Además:

```sql
SELECT estado, commit_sha FROM cm.repo_snapshots WHERE tenant_id = 'org_xxx' ORDER BY iniciado_en DESC LIMIT 1;
```

debe ser `completo` y el `commit_sha`, el HEAD del repositorio. Si un recuento queda fuera de tolerancia: no continuar;
ver §7.

### 3.5 Restaurar lo que no se reconstruye

* **Capa semántica y humana** (`cm.definitions`, `cm.concepts`, aristas `REALIZED_BY`/`RELATES`/`EXPRESSES`): si el
  desastre la afectó, se restaura de la copia de seguridad ([recuperacion_cm_hnsw.md](recuperacion_cm_hnsw.md) §2). Los
  ids de símbolo son deterministas (`uuid5`): lo restaurado vuelve a enlazar con los símbolos reconstruidos.
* **Vectores** (`vec.*_embeddings`): borrar el grafo borra en cascada los de los nodos eliminados. Restaurar de la
  copia o repararlos con §5.

### 3.6 Pérdida conocida: descripciones de símbolos

`cm.vaciar_grafo_tenant` borra `cm.files` y, en cascada, `cm.symbols`. Las **descripciones y roles de símbolo
generados por LLM** viven en `cm.symbols`, así que se pierden con el alcance determinista aunque la política las marque
«reconstruir, sin LLM». El ensayo mide cuántas se pierden. Si importan, restaurar `cm.symbols` del backup **antes** de
reconstruir, o regenerarlas con una ingesta completa (camino B). Está planteado a Database (§8).

## 4. Camino B: reconstrucción completa

1. §3.1 y, como `kallicode_admin`: `SELECT * FROM cm.vaciar_grafo_tenant('org_xxx', 'completo', 'org_xxx');`
2. `uv run python -m kallicode_codemapping.build_graph --full` (ingesta entera con LLM y TEI; necesita presupuesto de
   LLM y TEI disponible; es el camino lento y con coste).
3. Refrescar el cierre transitivo si el log no lo indica: con `app.tenant_id` fijado,
   `SELECT * FROM cm.refrescar_reachability();`
4. Validar como §3.4. La capa semántica se vuelve a producir y puede **no ser idéntica** (es salida de un LLM): se
   compara por recuentos, no por contenido.

## 5. Reparar embeddings (sin LLM)

```sql
SELECT * FROM vec.vigencia_embeddings();        -- qué falta o está obsoleto
SELECT * FROM vec.embeddings_a_reparar(100);
```

Se reparan solos al final del siguiente refresco incremental (`build_graph --refresh`), con TEI, en lotes de
`CM_VIGENCIA_MAX_REEMBEBIDOS` (5000 por corrida). Detalle en [vigencia_embeddings_hnsw.md](vigencia_embeddings_hnsw.md).
Los índices HNSW se reconstruyen según [recuperacion_cm_hnsw.md](recuperacion_cm_hnsw.md) §5.

## 6. El ensayo (y cómo repetirlo en staging)

El ensayo automatiza este mismo procedimiento sobre un grafo de referencia y produce el **acta**:

```bash
KC_DATABASE_URL_MIGRATIONS=<superusuario> KC_BENCH_ING_DIVISOR=1 KC_ENSAYO_ENTORNO="staging <nombre>" \
  uv run python packages/codemapping/tests/ensayo_reconstruccion_grafo.py --acta acta.md --json datos.json
```

* Código de salida 0 = **CUMPLE**; 1 = incumple (el acta lista qué).
* Corre cada mes en `.github/workflows/ensayo_reconstruccion_mensual.yml` (día 1, 03:40 UTC; también a mano con
  *Run workflow*). El acta queda como artefacto del job (90 días) y en su resumen.
* El ensayo mensual usa un grafo **sintético** de 200.000 nodos con LLM, TEI y base del cliente simulados. El ensayo
  **en staging** (con el repositorio y la base del cliente reales) lo ejecuta una persona con este runbook:
  §3 completo sobre un tenant de staging, con la hoja de resultados de §9. Guardar el acta firmada en `docs/`.

## 7. Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `vaciar_grafo_tenant` rechaza la confirmación | El tercer argumento no repite el tenant | Repetir exactamente el identificador |
| `permission denied` al vaciar | No se usa `kallicode_admin` | Reconectar con ese rol |
| El build termina sin `reconstrucción determinista completa` | Interrupción o repositorio inaccesible | Relanzar el mismo comando; revisar `CM_REPO_URL` |
| Un recuento fuera del 0,5 % | El repositorio cambió desde la referencia o el parser difiere | Comparar contra el HEAD con el ensayo; si persiste, escalar al equipo de CodeMapping con el acta |
| `reachability` en 0 | El cierre transitivo falló (solo se registra) | `SELECT * FROM cm.refrescar_reachability();` con `app.tenant_id` fijado, tras `SELECT * FROM cm.analizar_tablas();` |
| Búsqueda semántica vacía tras reconstruir | Faltan embeddings | §5 |

## 8. Límites y pendientes

* RPO/RTO del esquema `cm` **propuestos**, no aprobados: acordarlos con Database y reflejarlo en
  `core.objetivos_recuperacion`.
* Pérdida de descripciones de símbolo con alcance determinista (§3.6): decidir con Database si `cm.symbols` pasa a
  «mixta» o si se conserva la descripción al reconstruir.
* El borrado es en el sitio: sin conmutación de `cm.snapshot_vigente`, el tenant queda sin grafo durante la
  reconstrucción.
* La ejecución en staging real con repositorio y base del cliente reales no está hecha: esta entrega incluye un ensayo
  sintético automatizado y mensual, y el procedimiento para hacerlo en staging.

## 9. Hoja de resultados y revisión

| Medida | Valor |
|---|---|
| Tenant / entorno | |
| Inicio del procedimiento | |
| Fin (recuentos validados) | |
| Tiempo de recuperación (min) vs RTO | |
| Recuento más desviado (objeto, %) | |
| Descripciones de símbolo perdidas | |

| Revisión | Nombre | Fecha |
|---|---|---|
| Revisado por Database | | |
| Probado por persona ajena al equipo | | |
