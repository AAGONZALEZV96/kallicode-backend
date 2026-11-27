# Kallicode Backend

Monorepo del backend de Kallicode: API del Portal Cliente (`/api/v1`),
webhooks de integraciones (`/api/v1/webhooks`), API interna del pipeline
(`/internal/v1`) y workers asíncronos. FastAPI · PostgreSQL 16 + pgvector ·
Redis Streams · multi-tenant con RLS.

Documentos de referencia (en la carpeta del proyecto):
- «Diseño del backend — Especificación de endpoints» v1.0 (79 endpoints)
- «Modelo de Base de Datos» v1.0 (41 tablas; los .sql de `db/` son la fuente de verdad)
- «Especificación del Entorno de Desarrollo» v1.0 (decisiones D01–D17)

## Arranque local

```bash
make bootstrap   # uv sync + hooks
make up          # postgres+pgvector, redis, azurite, mailpit, embeddings (TEI BGE-M3)
make db          # inicializa una base nueva con esquema, migraciones y seeds
make api         # http://localhost:8000/docs
```

Si la base ya está inicializada, omite `make db`. Para iniciar la interfaz,
sigue el [README del Portal](kallicode-portal/README.md).

Usuarios seed (tenant `org_demo`, contraseña `Dev12345!demo`):
`owner@demo.kallicode.dev`, `admin@…`, `arquitecto@…`, `aprobador@…`, `dev@…`.

### Ejecutar tickets desde el portal

1. Configura en `.env` el [par RSA local de servicio](docs/superficie-interna.md#generar-el-par-de-servicio-local).
   La API interna y los workers deben cargar el mismo par.
2. Configura un proveedor LLM operativo en las variables `KC_LLM_*_BASE_URL`,
   `KC_LLM_*_MODEL` y `KC_LLM_*_API_KEY` que use tu entorno. Las URLs locales
   del ejemplo apuntan al puerto 8081. `make up` inicia PostgreSQL, Redis,
   Azurite, Mailpit y TEI para embeddings, y el proveedor LLM se levanta aparte.
3. Mantén la API pública corriendo. En terminales distintas inicia la API
   interna y los workers.

```bash
# Terminal de la API interna
uv run uvicorn kallicode_api.main_interna:app --host 127.0.0.1 --port 8001 --no-proxy-headers

# Terminal de los workers
make workers
```

En el portal, usa un tenant con la fábrica activa, una línea disponible y
cuota para procesar el ticket. El flujo de prueba hasta Gate 1 también puede
ejecutarse con respuestas LLM grabadas:

```bash
uv run pytest -q tests/e2e/test_pipeline_hasta_gate1.py
```

### Demo en el navegador con un LLM grabado

Usa un entorno local de prueba con tickets sintéticos. El proveedor de demo
escucha en `127.0.0.1:8082`. Con la API pública y
el portal corriendo, inicia estos tres procesos en terminales distintas.
Detén la API interna y los workers anteriores antes de sustituirlos.

```bash
# Terminal 1
make demo-llm

# Terminal 2
KC_PERSISTIR_TIER_FABLE=true uv run uvicorn kallicode_api.main_interna:app --host 127.0.0.1 --port 8001 --no-proxy-headers

# Terminal 3
make workers-demo
```

En el portal, crea un ticket de prueba sobre un fallo de inicio de sesión.
El proveedor usa respuestas sintéticas grabadas para generar la spec y
llegar a Gate 1. Termina la demo cuando aparezcan la spec y el gate.

### Portal en Vercel con un pipeline alojado en Render

Para una evaluación alojada, usa un tenant de prueba aislado, con la fábrica
activa, cuota y una línea disponible. El objetivo de la primera prueba es
crear un ticket sintético en el portal y ver una especificación real en Gate 1.

1. En un **Render Web Service Docker**, usa el `Dockerfile` de la raíz y
   `./start.sh` como Docker Command. Este servicio recibe tráfico en la API
   pública. El mismo contenedor inicia el worker y su API interna en
   `127.0.0.1:8001`, de modo que el acceso desde el portal despierta los tres.
2. En ese servicio configura PostgreSQL y Redis, los dos pares JWT y la bóveda.
   Define `KC_DATABASE_URL_WORKER` con el rol `kallicode_worker` de PostgreSQL.
   Para un entorno de pruebas que use `KC_ENV=local`, configura
   `KC_BLOB_CONNECTION_STRING` para una cuenta Azure Blob accesible por HTTPS,
   con un contenedor privado de evidencias. La spec y las trazas deben sobrevivir
   a reinicios de Render.
3. Configura un proveedor de IA alojado en **Render**. Las variables
   `KC_LLM_{FLASH,PRO,FABLE}_BASE_URL`, `_MODEL` y `_API_KEY` determinan el
   modelo de cada tier. Para un proveedor compatible con OpenAI en los tres
   tiers, agrega `KC_LLM_FABLE_PROTOCOLO=openai` y
   `KC_PERSISTIR_TIER_FABLE=true`. Si eliges Gemini para evaluar con tickets
   sintéticos, su URL compatible es
   `https://generativelanguage.googleapis.com/v1beta/openai`. Elige un modelo
   de texto disponible en tu proyecto y comprueba la cuota antes de probar.
4. En el proyecto **del portal** de Vercel, configura `VITE_API_URL` con la
   URL pública del servicio Render combinado y redespliega el portal. En
   Render, añade el origen del portal a `KC_CORS_ORIGINS` y permite el host
   público y `127.0.0.1` en `KC_ALLOWED_HOSTS`.
5. Abre el portal desplegado y crea un ticket sintético. Confirma en Render
   que `/health/ready` registra el pulso del orquestador y comprueba que el
   ticket llega a Diseño y muestra la spec y el Gate 1 en el portal.

El cómputo gratuito de Render puede suspender el servicio cuando queda sin
tráfico. Un trabajo pendiente se podrá atender cuando el servicio vuelva a
despertar. La ejecución con datos de clientes requiere aprobar el uso de su
código y contenido con el proveedor elegido.

## Estructura

```
packages/core/     kallicode_core: config, log central, errores, db (RLS),
                   seguridad (JWT/Argon2), auditoría (hash encadenado),
                   comercial (QU-1/2/3, RL-1/2/3) y llm/ (3 tiers)
apps/api/          kallicode_api: main + deps + routers/ (15) + servicios/
apps/workers/      kallicode_workers: normalizer, scheduler, billing_cycle,
                   notifier, housekeeping, audit_export
db/                01..05.sql (fuente de verdad) + migrations/ + seeds/
```

## Los tres tiers de LLM

| Tier  | Modelo            | Uso                                   |
|-------|-------------------|---------------------------------------|
| flash | DeepSeek V4 Flash | razonamiento simple, alto volumen     |
| pro   | DeepSeek V4 Pro   | razonamiento complejo                 |
| fable | Claude Fable 5    | muy complejo y orquestación           |

Escalado automático `flash → pro → fable` (fallo de esquema ×2, baja
confianza o proveedor caído); si fable falla, escalado a humano (§17.12).
Catálogo completo de llamadas documentadas: `kallicode_core/llm/plantillas.py`
(y expuesto en `GET /api/v1/models/assignments`).

## Log central

Todas las transacciones (HTTP, negocio, LLM, workers) emiten JSON por línea a
stdout con `trace_id`, `tenant_id` y actor (`kallicode_core/logging.py`);
Azure Monitor lo recoge en cloud. Los eventos de negocio se sellan además en
la cadena de auditoría inmutable (`audit.audit_events`).

## Telemetría del backend

Prometheus consulta `GET /metrics` en el listener privado. Usa un target lógico
por entorno porque los contadores de las réplicas se reúnen en Redis. Carga las
reglas de `infra/prometheus/backend-alertas.yml` en `rule_files`. La longitud de
streams mide entradas retenidas (`XLEN`), y el pulso del orquestador caduca tras
20 segundos. El worker líder comprueba las cadenas de auditoría cada cinco minutos.
`kc_metricas_fuente_disponible` indica si Redis o PostgreSQL respondieron al scrape.

Configura `KC_LLM_PRECIOS_USD_POR_MILLON` con las tarifas reales en USD de
entrada y salida por tier. Las series de consumo usan 30 días móviles. El coste
medio por ticket cerrado se desglosa por etapa. La alerta compara tickets
cerrados durante las últimas 24 horas con la mediana de los 29 días anteriores.
Los tickets con consumo sin tarifa se excluyen del cálculo de coste. Al actualizar
ambos procesos, despliega la API interna antes de los workers para preservar el
registro único de cada consumo.

Para conciliar un periodo, exporta los cargos del proveedor correspondientes al
pipeline, con el mismo intervalo y excluyendo impuestos, créditos, ingesta y
sondas. Normaliza el export a un CSV con columnas `tier,coste_usd`, una línea por
tier. Usa las tarifas del periodo, separando los periodos si cambiaron.

```bash
uv run python -m kallicode_api.conciliar_llm proveedor.csv \
  --desde 2026-11-01T00:00:00Z --hasta 2026-11-02T00:00:00Z
```

El comando compara estimaciones con cargos observados y sale con código 1 si
la diferencia alcanza el 10 % en algún tier o en el total.

## Convenciones

- Los `.sql` mandan: el código no define esquema (D06). Cambios → `db/migrations/NNN_*.sql`.
- Errores: sobre JSON `{error:{codigo,mensaje,detalle,trace_id,timestamp}}`;
  códigos estables documentados en cada endpoint (docstring) y en §18 del diseño.
- Docstrings en español con entrada/salida/errores/log/comercial por endpoint.
