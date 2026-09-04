# Kallicode Backend

Monorepo del backend de Kallicode: API del Portal Cliente (`/api/v1`),
webhooks de integraciones (`/api/v1/webhooks`), API interna del pipeline
(`/internal/v1`) y workers asíncronos. FastAPI · PostgreSQL 16 + pgvector ·
Redis Streams · multi-tenant con RLS.

Documentos de referencia (en la carpeta del proyecto):
- «Diseño del backend — Especificación de endpoints» v1.0 (79 endpoints)
- «Modelo de Base de Datos» v1.0 (41 tablas; los .sql de `db/` son la fuente de verdad)
- «Especificación del Entorno de Desarrollo» v1.0 (decisiones D01–D17)

## Arranque local (<15 min)

```bash
make bootstrap   # uv sync + hooks
make up          # postgres+pgvector, redis, azurite, mailpit, embeddings (TEI BGE-M3)
make db          # aplica db/01..05.sql + migraciones + seeds
make api         # http://localhost:8000/docs
make workers     # en otra terminal
```

Usuarios seed (tenant `org_demo`, contraseña `Dev12345!demo`):
`owner@demo.kallicode.dev`, `admin@…`, `arquitecto@…`, `aprobador@…`, `dev@…`.

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

## Convenciones

- Los `.sql` mandan: el código no define esquema (D06). Cambios → `db/migrations/NNN_*.sql`.
- Errores: sobre JSON `{error:{codigo,mensaje,detalle,trace_id,timestamp}}`;
  códigos estables documentados en cada endpoint (docstring) y en §18 del diseño.
- Docstrings en español con entrada/salida/errores/log/comercial por endpoint.
