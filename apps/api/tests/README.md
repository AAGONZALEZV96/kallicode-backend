# Fixtures de prueba del backend

| Fixture | Ubicación | Qué hace |
|---|---|---|
| `redis_doble` | `conftest.py` (raíz) | Doble de Redis en memoria con espacio de nombres por prueba. Cubre get/set/setex/incr/expire/ttl/delete/exists/ping. Los Streams (xadd, xack, xautoclaim, xgroup_create, xreadgroup) no están implementados. |
| `seed_dos_tenants` | `apps/api/tests/conftest.py` | Crea dos organizaciones, las commitea y las borra al terminar. |
| `usuarios_por_rol` | idem | Crea un usuario real (fila en `core.users`) por cada uno de los seis roles en cada uno de los dos tenants. Devuelve `{tenant_id: {rol: user_id}}`. Depende de `seed_dos_tenants` y se borra al terminar. |
| `crear_ticket` | idem | Factory de tickets con commit explícito. |
| `client` | idem | Cliente httpx contra la app ASGI. |
| `auth_headers(tenant_id, rol)` | idem | Simula un usuario autenticado con uno de los seis roles: owner, admin, architect, approver, member, viewer. |
| `db_admin` | idem | Sesión con BYPASSRLS, solo para sembrar datos. |

## Marcadores

Todo lo que está en `apps/api/tests/` se marca automáticamente como `integracion`
(necesita PostgreSQL y Redis reales). Lo define `pytest_collection_modifyitems` en el
`conftest.py` de la raíz.

## Cobertura

El umbral está en `[tool.coverage.report]` de `pyproject.toml` (lo gobierna Team 5).
Sube al cierre de cada sprint y nunca baja.