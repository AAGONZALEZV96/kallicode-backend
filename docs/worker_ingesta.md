# Worker de ingesta de CodeMapping (KC-CM-10-1)

Consumidor sobre Redis Streams que ejecuta el pipeline de ingesta (`full_build` / `run_refresh`)
con entrega *at-least-once*, reserva idempotente atómica, reintentos con backoff exponencial y DLQ.

## Evento

Stream `kc:codemapping:ingesta`, grupo `kc-codemapping`. Campos (todos string):

| Campo | Obligatorio | Notas |
|---|---|---|
| `tenant_id` | sí | |
| `repo_id` | sí | |
| `repo_url` | sí | |
| `commit_sha` | sí | 7–64 hex; se normaliza a minúsculas |
| `modo` | no | `refresh` (por defecto) o `full` |
| `trace_id` | no | si falta, el worker genera uno |

La clave de idempotencia es `(tenant_id, "codemapping.ingesta", repo_id:commit_sha)` sobre
`core.idempotency_keys`.

## Garantías

- **Idempotencia atómica.** `INSERT ... ON CONFLICT DO NOTHING` con `status_code = 0` reserva el
  evento. Un duplicado se confirma (XACK) sin ejecutar el pipeline: no duplica snapshots, ficheros,
  símbolos ni aristas. Si la reserva quedó sin latido más de `lease_segundos` (worker caído), un
  único worker la toma por compare-and-swap.
- **Latido.** Mientras corre el pipeline, un hilo renueva la reserva; un pipeline largo no pierde
  la exclusividad.
- **Respaldo en el grafo.** Si la clave se purgó por TTL pero el commit ya tiene un snapshot
  `completo`, no se reprocesa y la clave se recompone.
- **At-least-once.** El mensaje se confirma solo después de terminar. Los pendientes de un
  consumidor caído se recuperan con XAUTOCLAIM tras `reclamar_inactivos_ms`.
- **Reintentos.** Ante un fallo se libera la reserva y el evento se reprograma en
  `kc:codemapping:ingesta:retry` (ZSET) con `base * 2^(intento-1)` segundos, tope `backoff_max`.
  Un script Lua lo devuelve al stream una sola vez aunque haya varios workers.
- **DLQ.** Agotados `max_intentos` (o ante `ErrorNoRecuperable` / evento inválido) se escribe en
  `kc:dlq` con `trace_id`, `attempts`, `original_stream`, `tenant_id`, `error` y
  `error_estructurado` (JSON: `tipo`, `mensaje`, `frames`), se incrementa
  `kc:metrics:dlq:<stream>` y se confirma el mensaje, por lo que el grupo no se bloquea.
- **Sin credenciales en trazas.** `repo_url` y los mensajes de error se enmascaran
  (`scheme://usuario:***@`) antes de loguear o enviar a la DLQ.

## Configuración (variables `CM_WORKER_*`)

| Variable | Defecto |
|---|---|
| `CM_WORKER_REDIS_URL` / `CM_WORKER_REDIS_PASSWORD` | — |
| `CM_WORKER_STORE_DATABASE_URL` | — (rol `kallicode_worker`) |
| `CM_WORKER_MAX_INTENTOS` | 5 |
| `CM_WORKER_BACKOFF_BASE_SEGUNDOS` / `_MAX_SEGUNDOS` | 2.0 / 300 |
| `CM_WORKER_LEASE_SEGUNDOS` | 3600 |
| `CM_WORKER_RECLAMAR_INACTIVOS_MS` | 60000 |
| `CM_WORKER_BLOQUEO_MS` / `CM_WORKER_LOTE` | 2000 / 1 |

Ejecución: `uv run --package codemapping python -m kallicode_codemapping.worker`.

## Base de datos

La migración `054_worker_ingesta_idempotencia.sql` otorga `INSERT, UPDATE` sobre
`core.idempotency_keys` a `kallicode_worker` (el DELETE y el SELECT ya existían por la política de
colas). No crea índices.

## Pruebas

- `test/test_worker_unit.py`: lógica con Redis y almacén simulados.
- `tests/test_worker_pg_redis.py`: Redis y PostgreSQL reales (se omite si no están). Cubre
  reinyección 5×, reserva con 8 hilos concurrentes, vencimiento de lease, latido, rol worker,
  clave purgada, reintento transitorio, DLQ sin bloquear el grupo, evento inválido, mensaje sin
  ACK recuperado y promoción única con dos workers.
  Variables: `CM_TEST_REDIS_URL` (por defecto `redis://localhost:6379/0`), `CM_TEST_REDIS_PASSWORD`,
  `KC_DATABASE_URL_MIGRATIONS`.

## Decisiones a confirmar con el equipo

1. Nombre del stream y campos del evento (productor: Mario / orquestador).
2. Reutilizar `core.idempotency_keys` con el `endpoint` `codemapping.ingesta`; la purga por TTL de
   KC-DB-10-3 debe respetar las reservas con `status_code = 0` vigentes.
3. El snapshot existente se busca por `(tenant, commit_sha)`; si dos repos del mismo tenant
   comparten commit, el segundo se considera ya procesado.
