# Curación de CodeMapping (KC-CM-07-3, 07-4, 07-5)

Los enlaces y conceptos con confianza inferior al umbral (0,75 por defecto) no entran al grafo:
quedan en `cm.curation_queue` hasta que una persona los aprueba o rechaza.

## Forma de la cola

`cm.curation_queue(id, tenant_id, tipo, payload, estado, revisado_por, revisado_en, creado_en)`.
Dos orígenes escriben con la misma forma de `payload`:

| Origen | tipo | payload |
|---|---|---|
| Trigger `cm.edges_confianza_trg` | `enlace` | `tipo, src_kind, src_id, dst_kind, dst_id, confidence, umbral, snapshot_id` |
| Trigger `cm.concepts_confianza_trg` | `concepto` | `id, name_en, name_local, descripcion, confidence, umbral, snapshot_id` |
| `GraphLoader.load_curation_queue` | `enlace` | `tipo, src_id, dst_id, confidence, snapshot_id, evidencia{raw_link, motivo}` |

El cargador no reabre elementos ya resueltos: si el elemento sigue `pendiente` se refresca su
payload; si una persona ya lo aprobó o rechazó, se deja como está.

## Endpoints (`/api/v1/codemapping/curation`)

| Método y ruta | Rol | Qué hace |
|---|---|---|
| `GET /selectores` | cualquiera | Opciones de los filtros: estados y tipos (con conteo), snapshots (con pendientes), rango de confianza y umbral. |
| `GET /` | cualquiera | Lista paginada. Filtros: `estado`, `tipo`, `snapshot_id`, `confianza_min`, `confianza_max`, `limite` (1–100), `desde`. Los más dudosos primero. |
| `GET /{id}` | cualquiera | Detalle con el payload completo saneado. |
| `POST /{id}/decision` | owner, admin, architect, approver | Cuerpo `{decision: aprobado\|rechazado, comentario?}`. Solo resuelve `pendiente`; si no, 409 `TRANSICION_INVALIDA`. Deja evento `curacion_resuelta` en la auditoría. |

Un id de otro tenant responde 404 (RLS).

## Anti-injection (KC-CM-07-4)

- **Entrada:** el texto que viene del código se sanea (controles, ancho cero, bidi) y la
  descripción de cada definición va delimitada con un nonce por lote; los prompts del Map y del
  Reduce declaran que lo delimitado es dato.
- **Salida:** cada respuesta del LLM se valida contra el esquema (`name_en`, `category` y
  `synonyms` con patrón estricto; `description_en` acotada) y se descartan los conceptos con frases
  de inyección. La validación corre también entre Map y Reduce, para cortar la inyección de segundo
  orden. Los descartados se registran y pueden recuperarse con el parámetro `rechazados`.
- **Cola y API:** el razonamiento del LLM se sanea al encolar y otra vez al servirlo. La UI debe
  pintar todo como texto, nunca como HTML.

## Pendiente de decisión

Aprobar un elemento hoy **solo cambia su estado**: no inserta la arista ni el concepto en el
grafo. Promoverlo requiere decidir cómo saltarse el trigger de umbral (por ejemplo, un marcador
`curado` en `props`) y se probaría contra PostgreSQL real.
