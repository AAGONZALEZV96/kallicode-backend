# Operación de la ingesta de CodeMapping

KC-CM-12-3 · Para quien tenga que **ejecutar una ingesta** del repositorio de un cliente sin conocer el código de
CodeMapping. Se puede seguir de arriba abajo. Qué hace cada fase está en el
[README del paquete](../../packages/codemapping/README.md); qué hay en el grafo, en el
[catálogo](../catalogo_grafo_cm.md).

| Si necesitas… | Ve a |
|---|---|
| Probar la instalación en tu máquina en 10 minutos | §2 |
| La lista de variables | §3 |
| Ingerir un cliente en producción | §4 |
| Refrescar un grafo existente | §5 |
| Saber si la ingesta salió bien | §6 |
| Resolver un fallo | §8 |
| Recuperar un grafo perdido | [Runbook de reconstrucción](../runbook_reconstruccion_grafo.md) |

## 1. Conceptos mínimos

- **Tenant**: la organización cliente (`org_…`). Todo el grafo lleva su `tenant_id` y RLS: un tenant nunca ve el
  grafo de otro. El tenant debe existir en `core.organizations` antes de ingerir.
- **Snapshot**: cada ingesta es una fila de `cm.repo_snapshots` con el commit, el estado (`en_progreso`,
  `completo`, `fallido`, `pendiente_intervencion`) y las estadísticas por fase.
- **Alcance**: `completo` hace las siete fases (necesita LLM y TEI); `determinista` hace solo las que salen del
  repositorio y de la base del cliente (sin LLM ni TEI, gratis y en minutos).
- **Reanudación**: si una ingesta muere, relanzar **el mismo comando** sobre el mismo commit retoma el snapshot desde
  su último checkpoint. No hace falta limpiar nada.

## 2. Primera ingesta en local (ensayo guiado)

Sirve para comprobar que el entorno funciona antes de tocar un cliente. Usa el repositorio de referencia del propio
paquete. Probado el 2026-11-17 en Windows (Git Bash) con PostgreSQL 16 en Docker: la ingesta determinista tardó 6 s.

### 2.1 Requisitos

- Docker, `uv` y `git`. El repositorio `kallicode-backend` clonado y `uv sync --all-packages` hecho.
- La base de la plataforma con todas las migraciones: `make up && make db` (crea también el tenant de prueba
  `org_demo` desde `db/seeds/dev.sql`).

### 2.2 Preparar un repositorio de prueba

El pipeline clona el repositorio y usa la rama **`main`**: el repositorio tiene que tenerla.

```bash
cp -r packages/codemapping/tests/fixtures/repo_ref /tmp/cm-demo
cd /tmp/cm-demo && git init -q -b main && git add -A && git commit -qm demo && cd -
```

### 2.3 Variables

```bash
export CM_TENANT_ID=org_demo
export CM_REPO_URL=/tmp/cm-demo
export CM_WORKDIR=/tmp/cm-workdir
# Rol kallicode_app: es el que tiene permisos sobre el esquema cm.
export CM_STORE_DATABASE_URL=postgresql+psycopg://kallicode_app:dev@localhost:5432/kallicode
```

### 2.4 Ingesta determinista

```bash
uv run python -m kallicode_codemapping.build_graph --full --alcance determinista
```

El log es JSON, una línea por evento. Debe acabar en `reconstrucción determinista completa @ <commit>`.

### 2.5 Comprobar el resultado

Con cualquier cliente SQL, como `kallicode_admin`:

```sql
SELECT * FROM cm.recuentos_grafo('org_demo') ORDER BY objeto;
```

Con el repositorio de referencia sale exactamente esto:

| objeto | filas |
|---|---:|
| `arista:CALLS` | 6 |
| `nodo:file` | 13 |
| `nodo:symbol` | 84 |
| `reachability` | 6 |

Sin `CM_DATABASE_URL` no hay tablas del cliente, por eso no aparecen `db_table` ni `USES_TABLE`.

### 2.6 Ingesta completa (con LLM y TEI)

Añade a las variables de §2.3 un TEI con BGE-M3 y un proveedor LLM compatible con OpenAI:

```bash
docker compose up -d embeddings          # TEI en http://localhost:8080 (la primera vez descarga el modelo)
export CM_TEI_ENDPOINT_URL=http://localhost:8080
export CM_LLM_BASE_URL=<url-del-proveedor>/v1
export CM_LLM_API_KEY=<clave>             # nunca en el repositorio; en producción sale de Key Vault
uv run python -m kallicode_codemapping.build_graph --full
```

> **Bloqueo conocido (2026-11-17).** La ingesta `completo` falla hoy antes de llamar al LLM, en la estimación de
> presupuesto: `function core.ciclo_inicio(text, timestamp with time zone) does not exist`. La consulta de
> `budget.verificar_presupuesto_tenant` no coincide con el esquema actual. Hasta que se corrija, solo la ingesta
> `determinista` es ejecutable de principio a fin. Ver §8.

## 3. Configuración

Variables `CM_*` del pipeline (`kallicode_codemapping.config.Settings`). Un valor en `.env` también vale.

| Variable | Defecto | Para qué |
|---|---|---|
| `CM_TENANT_ID` | — (obligatoria) | Tenant a ingerir |
| `CM_REPO_URL` | — (obligatoria) | URL o ruta del repositorio; se clona la rama `main` |
| `CM_STORE_DATABASE_URL` | `postgresql+psycopg://postgres:dev@localhost:5432/kallicode` | Base de Kallicode, **rol `kallicode_app`** |
| `CM_DATABASE_URL` | vacía | Base del cliente (fase 3). Sin ella no hay tablas, columnas, `REFERENCES` ni `USES_TABLE` |
| `CM_CLIENT_DB_MAX_CONNECTIONS` | 2 | Conexiones simultáneas a la base del cliente |
| `CM_CLIENT_DB_STATEMENT_TIMEOUT_MS` | 30000 | Tiempo máximo por sentencia contra la base del cliente |
| `CM_WORKDIR` | `./workdir` | Clones y checkpoints (`<workdir>/<tenant>`) |
| `CM_MAX_FILE_BYTES` | 500000 | Archivos más grandes se ignoran |
| `CM_TEI_ENDPOINT_URL` | `http://localhost:8080` | TEI con BGE-M3 |
| `CM_TEI_BATCH_SIZE` | 32 | Textos por petición a TEI (bájalo a 8 con TEI en CPU) |
| `CM_LLM_BASE_URL` / `CM_LLM_API_KEY` | DeepSeek / `test_key` | Proveedor LLM. La clave real se inyecta desde Key Vault |
| `CM_MODEL_FLASH` / `CM_MODEL_PRO` | `deepseek-chat` / `deepseek-coder` | Modelos para anotación y para ontología |
| `CM_LINK_CONFIDENCE_THRESHOLD` | 0.75 | Por debajo, un enlace va a la cola de curación |
| `CM_DOCS_PRESUPUESTO_TOKENS` / `_SEGUNDOS` | 2000000 / 1800 | Tope de la fase documental por corrida |
| `CM_VIGENCIA_MAX_REEMBEBIDOS` | 5000 | Embeddings que el refresco repara por corrida |

El resto (documentación, nuggets, Diseño) tiene valores por defecto razonables; están en `config.py` y en
`.env.example`. Las del worker llevan el prefijo `CM_WORKER_` ([worker_ingesta.md](../worker_ingesta.md)).

**Roles de base de datos.** El pipeline escribe con `kallicode_app` (`CM_STORE_DATABASE_URL`). El worker guarda su
estado de idempotencia con `kallicode_worker` (`CM_WORKER_STORE_DATABASE_URL`). Con `kallicode_worker` en
`CM_STORE_DATABASE_URL` la ingesta falla con `permission denied for schema cm`.

## 4. Ingesta en producción

En producción la ingesta la ejecuta el **worker** de CodeMapping, no una persona: consume eventos del stream de
Redis `kc:codemapping:ingesta` y garantiza que el mismo commit no se procese dos veces. Detalle de garantías,
reintentos y DLQ en [worker_ingesta.md](../worker_ingesta.md).

### 4.1 Antes de lanzar

1. El tenant existe en `core.organizations` y tiene suscripción activa.
2. Credenciales del repositorio (y de la base del cliente, si se da) registradas en la bóveda del tenant; nunca en
   variables escritas a mano.
3. El worker está en marcha (`uv run --package codemapping python -m kallicode_codemapping.worker`) y su `/metrics`
   responde (puerto 9108).
4. Para un cliente nuevo, haz primero una ingesta `determinista` (§2.4 con sus datos): confirma acceso al
   repositorio y a la base del cliente sin gastar LLM.

### 4.2 Lanzar

El onboarding del portal debe publicar el evento; hasta que el orquestador lo haga, se publica a mano:

```bash
redis-cli XADD kc:codemapping:ingesta '*' \
  tenant_id org_xxx repo_id <id-del-repo> repo_url <url> commit_sha <sha-completo> modo full
```

`modo` es `full` para la primera ingesta y `refresh` (por defecto) para las siguientes.

### 4.3 Seguir el progreso

```sql
SET app.tenant_id = 'org_xxx';
SELECT id, estado, fase_actual, iniciado_en, finalizado_en, stats
  FROM cm.repo_snapshots ORDER BY iniciado_en DESC LIMIT 1;
```

`fase_actual` avanza por `ingesta` → `semantica` → `vectores` → `completo` (la determinista salta de `ingesta` a
`completo`). `stats` lleva los recuentos y la duración
de cada fase. En Grafana, el panel `infra/grafana/codemapping-worker-dashboard.json`.

### 4.4 Si se queda en `pendiente_intervencion`

El worker pausó la ingesta porque reintentar no lo arregla (credenciales caducadas, repositorio corrupto, LLM sin
respuesta válida). El motivo está en `stats.escalacion` y Backend recibió una escalación. Corrige la causa y reanuda
según [worker_ingesta.md](../worker_ingesta.md#escalación-humana-kc-cm-10-6).

## 5. Refresco

```bash
uv run python -m kallicode_codemapping.build_graph --refresh
```

Procesa solo los archivos cambiados desde el último snapshot, borra en cascada lo que desapareció y reembebe los
vectores obsoletos. Si detecta un *force-push* (historia reescrita), pasa solo a una ingesta completa. En producción, el
mismo evento de §4.2 con `modo refresh`.

## 6. Cómo saber que salió bien

Una ingesta está bien cuando se cumplen las tres:

1. El último snapshot del tenant está en `completo` y su `commit_sha` es el que se quería ingerir.
2. `SELECT * FROM cm.recuentos_grafo('org_xxx')` tiene `nodo:file` y `nodo:symbol` mayores que 0 y, con base del
   cliente, `nodo:db_table` y `arista:USES_TABLE`. En un refresco, ningún recuento cambia más de lo que cambió el
   repositorio.
3. Con alcance `completo`: `nodo:definition` y `nodo:concept` mayores que 0, y la cola de curación tiene los enlaces
   dudosos (`SELECT estado, count(*) FROM cm.curation_queue GROUP BY 1`).

## 7. Observabilidad

- **Logs**: JSON con `trace_id`, `tenant_id` y `phase` (`1/7` … `7/7`). El `trace_id` es el mismo en el snapshot y
  en la DLQ.
- **Métricas** (`/metrics` del worker): `kc_codemapping_ingesta_fase_duracion_segundos`,
  `kc_codemapping_worker_eventos_total`, `kc_codemapping_worker_errores_total`, `kc_codemapping_dlq_mensajes`,
  `kc_codemapping_stream_longitud`, `kc_codemapping_tei_embeddings_duracion_segundos` y las de vigencia de embeddings
  e índices HNSW ([vigencia_embeddings_hnsw.md](../vigencia_embeddings_hnsw.md)).
- **Coste**: el consumo de la fase documental (`origen = 'codemapping_docs'`) y de la realimentación
  (`codemapping_feedback`) queda en `core.usage_tokens` con su `snapshot_id`. **El enriquecimiento de las fases 4–5
  todavía no se registra** (origen previsto `codemapping_codigo`): su coste solo se ve en los logs de la estimación en
  seco (`phase: dry-run`). Se corrige junto con el bloqueo de §2.6.

## 8. Problemas frecuentes

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `permission denied for schema cm` | `CM_STORE_DATABASE_URL` usa `kallicode_worker` | Usar el rol `kallicode_app` |
| `git clone … Remote branch main not found` | El repositorio no tiene rama `main` | Hoy la rama no es configurable: crear `main` o pedir el cambio a CodeMapping |
| `function core.ciclo_inicio(…) does not exist` | Bloqueo conocido de la ingesta `completo` (§2.6) | Usar `--alcance determinista` hasta que se corrija |
| Falla con `violates foreign key … organizations` | El tenant no existe | Crearlo en `core.organizations` (en local, `make db` carga `org_demo`) |
| `BudgetExceededError` | La estimación de tokens supera la cuota del tenant | Revisar el consumo del ciclo; no relanzar en bucle |
| Error de conexión a TEI | TEI no arrancado o aún descargando el modelo | `curl <tei>/health`; esperar a `Ready` en su log |
| Se cortó a mitad | Caída del proceso o de la base | Relanzar el mismo comando: reanuda el snapshot |
| Snapshot `fallido` repetido en el mismo commit | Error determinista (p. ej. credenciales) | Leer el error en el log o en la DLQ (`kc:dlq`) antes de relanzar |
| Búsqueda semántica vacía | Faltan embeddings | `SELECT * FROM vec.vigencia_embeddings();` y refresco (§5) |

## 9. Límites conocidos

- La ingesta `completo` no se puede ejecutar hasta que se corrija la consulta de presupuesto (§2.6).
- La rama a ingerir es siempre `main`: `Settings` no tiene `target_branch` ni `default_branch`, aunque
  `ingest_repo.resolver_rama_tenant` los busca.
- Nadie publica todavía el evento de ingesta desde el onboarding (§4.2).
- El pipeline no llama a `cm.publicar_snapshot`: `cm.snapshot_vigente` queda vacío y `cm.reachability` nunca queda
  vigente, así que el impacto se resuelve siempre por el CTE recursivo. El resultado es correcto; solo se pierde la
  aceleración de KC-CM-11-2 ([ADR-018](../adr/ADR-018-grafo-en-postgresql.md#desviaciones)).

## 10. Registro de validación

Criterio de aceptación de KC-CM-12-3: una persona ajena al equipo de CodeMapping ejecuta una ingesta completa
siguiendo solo esta guía.

| Fecha | Persona (equipo) | Entorno | Alcance | Resultado | Dudas o correcciones a la guía |
|---|---|---|---|---|---|
| 2026-11-17 | Autor de la guía (ensayo previo) | Local, PostgreSQL 16 en Docker | `determinista` | Correcto: recuentos de §2.5 | Añadidos el rol `kallicode_app`, la rama `main` y el bloqueo de `completo` |
| | | Sprint Review | | | |
