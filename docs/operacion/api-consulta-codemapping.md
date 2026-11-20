# API de consulta de CodeMapping — guía para Backend y Testing

KC-CM-12-3 · Para los equipos que **consumen** el grafo: Backend (agentes del pipeline, plantillas) y Testing
(selección e impacto de pruebas). Explica qué operaciones hay, cómo llamarlas y qué garantías dan. La definición
normativa es el contrato; esta guía no lo sustituye ni lo copia.

| Contrato | Archivo | Qué cubre |
|---|---|---|
| `cm-grafo-v1` 1.0.0 | [`contratos/cm-grafo-v1.json`](../../contratos/cm-grafo-v1.json) | Las diez operaciones de consulta, con esquemas de entrada y salida y presupuesto de latencia |
| `cm-consulta-v1` | [`contratos/cm-consulta-v1.schema.json`](../../contratos/cm-consulta-v1.schema.json) | Detalle de la resolución de `{{nuggets}}` (la operación `resolver_nuggets`) |
| `cm-incidentes-v1` | [`contratos/cm-incidentes-v1.schema.json`](../../contratos/cm-incidentes-v1.schema.json) | Detalle de `{{incidents}}` (la operación `resolver_incidentes`) |
| `internal-v1` | [`contratos/internal-v1.yaml`](../../contratos/internal-v1.yaml) | Las rutas HTTP, generadas de la API interna |

Versiones congeladas: `contratos/historial/cm-grafo-v1/<versión>.json`.

## 1. Cómo llamar

### Por HTTP (lo normal desde otro servicio)

API interna (`kallicode_api.main_interna`, puerto 8001), prefijo **`/internal/v1/codemapping/grafo`**, todo `POST`
con cuerpo JSON:

```http
POST /internal/v1/codemapping/grafo/impacto
Authorization: Bearer <JWT de servicio: iss kc-internal, aud kc-internal-api, svc orquestador|motor-pruebas|…>
Idempotency-Key: <uuid>            # la API interna la exige; en estas lecturas no se registra
X-Trace-Id: <trace>
Content-Type: application/json

{"ficheros": ["src/billing.py"], "k": 3}
```

- **El tenant es siempre el del token**. No hay parámetro de tenant: la sesión fija `app.tenant_id` y RLS hace el
  resto. Un id de otro tenant simplemente no existe para ti.
- Identidades y claves de servicio: [superficie-interna.md](../superficie-interna.md).

### En proceso (dentro del monorepo)

| Desde | Clase | Notas |
|---|---|---|
| Código async con `AsyncSession` (API, workers) | `kallicode_core.cm_grafo_sesion.ConsultaGrafoSesion` | La que usa el router HTTP |
| Código síncrono con psycopg (CodeMapping, scripts) | `kallicode_codemapping.consulta_grafo.ConsultaGrafoPostgres` | |

Las dos implementan el protocolo `kallicode_core.cm_grafo.ConsultaGrafo`, y los modelos Pydantic de entrada y salida
viven en `kallicode_core` (`cm_impacto`, `cm_busqueda`, `cm_consulta`, `cm_incidentes`). No consultes `cm.*` con SQL
propio: las tablas pueden cambiar; el contrato no.

## 2. Operaciones

| Operación | Ruta | Para qué | p95 comprometido |
|---|---|---|---:|
| `resolver_nodos` | `/resolver-nodos` | Qué ids existen en el tenant y de qué kind son | 500 ms |
| `semillas` | `/semillas` | Nodos de partida de un área: los nodos pedidos y cada fichero con sus símbolos | 500 ms |
| `impacto` | `/impacto` | **Quién depende** del área, hasta k saltos (aristas en sentido inverso) | 500 ms |
| `dependencias` | `/dependencias` | **De qué depende** el área, hasta k saltos | 500 ms |
| `subgrafo` | `/subgrafo` | Vecindad en los dos sentidos y las aristas entre esos nodos | 500 ms |
| `vecinos` | `/vecinos` | Aristas a un salto de un nodo, por sentido y tipo | 500 ms |
| `dependencias_ocultas` | `/dependencias-ocultas` | Código acoplado por una tabla compartida sin llamarse (`CALLS*` → `USES_TABLE` ←) | 500 ms |
| `buscar_hibrido` | `/busqueda` | Top-k por similitud en definiciones, documentación y nuggets, con filtros | 400 ms |
| `resolver_nuggets` | `/nuggets` | Resuelve `{{nuggets}}`, `{{module_conventions}}` y `{{nuggets_for_file}}` | 200 ms |
| `resolver_incidentes` | `/incidentes` | Resuelve `{{incidents}}`: incidentes a ≤ 2 saltos del área | 200 ms |

Cada presupuesto lo verifica una prueba del CI, citada en el campo `latencia.verificado_por` del contrato.
El benchmark nocturno mide además `impacto` y `dependencias` a 200.000 nodos: p95 de 16 ms y 2 ms
([benchmark-grafo.md](../benchmark-grafo.md)).

## 3. Recorridos: `impacto`, `dependencias`, `subgrafo`, `dependencias_ocultas`

Entrada común (`ConsultaGrafoIn`):

| Campo | Tipo | Defecto | Notas |
|---|---|---|---|
| `nodos` | lista de ids | `[]` | Hasta 500. Ids de `cm.nodes` |
| `ficheros` | lista de rutas | `[]` | Hasta 500. Cada ruta aporta el fichero y todos sus símbolos |
| `k` | entero 1–5 | 3 | Saltos máximos |
| `tipos` | lista de tipos de arista | `null` | `null` = los de dependencia (`CALLS`, `USES_TABLE`, `REFERENCES`, `HAS_COLUMN`) en impacto y dependencias; todos en subgrafo |

Salida (`RecorridoOut`): `semillas` que existen, `nodos` alcanzados con su distancia (las semillas a distancia 0) y
**`truncado`**.

Garantías y límites:

- **Límite de 5.000 nodos visitados.** Si se alcanza, `truncado = true` y el resultado es parcial: no lo trates como
  completo. Reduce `k` o el área.
- **Tiempo máximo de 5 s** por operación (`statement_timeout` de la sesión). Pasado ese tiempo la llamada falla; no
  devuelve un resultado a medias.
- Los ciclos del grafo cortan la rama (`CYCLE`): nunca cuelgan la consulta.
- Una semilla que no existe se ignora; mira `semillas` en la respuesta para saber cuáles se usaron.

Ejemplo — qué se ve afectado si cambio `src/billing.py`:

```json
{"ficheros": ["src/billing.py"], "k": 3}
```

## 4. Qué aristas hay realmente

Lee el [catálogo generado](../catalogo_grafo_cm.md) para los tipos. Dos cosas que afectan a las consultas:

- `DEFINES` y `HAS_COLUMN` existen en el contrato pero **el cargador no las escribe**: pertenecer a un fichero o a
  una tabla va por clave foránea. Para «los símbolos de un fichero» usa `ficheros` en la entrada o `semillas`; filtrar
  por `tipos: ["DEFINES"]` devuelve vacío.
- `USES_TABLE` sale del **fichero** (`file` → `db_table`), no del símbolo.

## 5. Errores

| HTTP | Código | Cuándo |
|---|---|---|
| 401 | — | Falta el token de servicio o la `Idempotency-Key`, o es un token de usuario |
| 403 | — | Identidad de servicio no declarada |
| 422 | validación | La entrada no cumple el esquema (campo desconocido, `k` fuera de 1–5, más de 500 nodos) |
| 503 | `DEPENDENCIA_NO_DISPONIBLE` | PostgreSQL no disponible: reintentar con backoff |

Los objetos de entrada y salida son `additionalProperties: false`: valida las respuestas con el esquema del contrato
en tus pruebas.

## 6. Cambios del contrato

Resumen de `x-politica` del contrato:

- **PATCH** solo textos; **MINOR** cambios compatibles (operación nueva, campo de entrada opcional, cota relajada,
  presupuesto más estricto); **MAJOR** cualquier cambio incompatible, que se publica como `cm-grafo-v2.json` y convive
  al menos un sprint con la v1.
- Es incompatible, entre otras cosas, **añadir un campo a una salida** (los consumidores validan con
  `additionalProperties: false`) y subir un presupuesto de latencia.
- Una versión entra en vigor cuando Backend y Testing la aprueban en `contratos/cambios-anunciados.yaml`. El CI
  (`packages/core/tests/test_cm_grafo.py`) falla si el contrato cambia sin versión nueva o si una versión publicada se
  modifica.

Fuera de la v1: el inventario de elementos de interfaz (lo resuelve Testing con `selectores-portal-v1`).

## 7. Para Testing

- Para el impacto de un cambio, usa `impacto` con los ficheros del diff y `k = 2` o `3`. Las pruebas cubiertas se
  enlazan en `cm.test_flows` / `cm.test_flow_covers`.
- `kallicode_testing.cm_client` consulta hoy las tablas `cm.*` directamente. Lo recomendable es moverlo a esta API, para
  que un cambio de esquema no lo rompa sin pasar por el contrato.
- En pruebas, usa como grafo el fixture `packages/codemapping/tests/fixtures/repo_ref` ingerido en local
  ([operación §2](ingesta-codemapping.md#2-primera-ingesta-en-local-ensayo-guiado)).
