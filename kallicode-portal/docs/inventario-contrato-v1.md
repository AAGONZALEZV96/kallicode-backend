# Inventario de campos consumidos vs contrato `/api/v1` (KC-PT-01-6, cierra PT-F27)

Qué endpoints de `/api/v1` llama el portal, qué campos lee de cada respuesta y en qué difiere
del backend. Sirve de base a KC-PT-04-2 (tipos generados desde OpenAPI + Zod).

- **Backend revisado:** `develop` en `88890dc` (routers de `apps/api/src/kallicode_api/routers/`).
- **Portal revisado:** `kallicode-portal/src` en la misma rama.
- **Fecha:** 2026-10-04. Actualizado el 2026-10-05: `auth/logout` (KC-PT-02-3), `users/me`
  (KC-PT-02-5) y login multi-organización de KC-BE-02-3 (D10).

## 1. Resumen

| Métrica                                                       | Valor |
| ------------------------------------------------------------- | ----- |
| Operaciones `/api/v1` en el backend (sin webhooks)            | 61    |
| Operaciones que consume el portal                             | 34    |
| Operaciones sin consumidor en el portal (sección 5)           | 27    |
| Divergencias encontradas                                      | 10    |
| Corregidas (KC-PT-01-6)                                       | 4     |
| Pendientes (backend, decisión de producto o ticket posterior) | 6     |

## 2. Hallazgo principal: el OpenAPI de `/api/v1` no describe las respuestas

`app.openapi()` declara las rutas y los cuerpos de entrada, pero casi ninguna respuesta: los
handlers devuelven `dict`, así que el esquema publicado es `{"type": "object"}` sin campos.
Solo `TokensOut` (login, refresh, invitations/accept) tiene modelo de respuesta.

Consecuencias:

- No se puede comparar el portal contra el OpenAPI campo a campo; este inventario se hizo
  leyendo el código de cada router y las consultas SQL.
- KC-PT-04-2 no puede generar tipos útiles hasta que existan `response_model` en `/api/v1`.
- `docs/congelar_contrato.py` congela solo `/internal/v1`, no `/api/v1`.

**Pedido al backend (KC-BE-01-5 / KC-BE-03-5):** declarar `response_model` Pydantic en cada
endpoint de `/api/v1` y congelar ese contrato igual que el interno.

## 3. Divergencias

| #   | Endpoint                                         | Divergencia                                                                                                                                                                                                               | Estado                                                                                                             |
| --- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| D1  | `GET /dashboard/lines`                           | `lineas[].etapa` es `core.jobs.estado` (16 valores: `gate1`, `pendiente_asignacion`, `deploy_preflight`…), no la etapa del ticket. El portal la mostraba como etapa y aparecía la clave sin traducir (`st.gate1`).        | **Corregido**: tipo `EstadoJob` + traducciones es/en/pt. Sugerencia backend: renombrar a `estado_job`.             |
| D2  | `POST /tickets/dedup-preview`, `/impact-preview` | `PreviewIn.texto` acepta máx. 2000 caracteres; el portal enviaba título + descripción completos (hasta ~10 200). Con textos largos el backend responde 422 y la previsualización desaparecía sin aviso.                   | **Corregido**: el portal recorta a `MAX_TEXTO_PREVIEW` (2000).                                                     |
| D3  | `POST /tickets`                                  | El portal tipaba un campo `aviso` que no existe; el backend envía `duplicado_potencial` (hoy siempre `null`).                                                                                                             | **Corregido** el tipo (el campo no se usaba).                                                                      |
| D4  | Dobles MSW (`src/mocks`)                         | Diez datos de prueba y cinco respuestas no coincidían con el backend (p. ej. conexión `"ok"` en vez de `"conectada"`, línea `"libre"` en vez de `"disponible"`, `regresion` con claves inexistentes).                     | **Corregido**, y los tipos ahora usan los valores reales para que `tsc` detecte el error.                          |
| D5  | `POST /tickets/{n}/gates/2/approve`              | Si hay criterios en falla, el backend exige `acepta_fallos: true` + comentario (si no, 422 `FALLOS_SIN_ACEPTAR`). El portal nunca envía `acepta_fallos`: un Gate 2 con fallos no se puede aprobar, solo pedir cambios.    | **Pendiente** → KC-PT-06-7 (modal de firma de gates).                                                              |
| D6  | `POST /tickets/{n}/gates/3/request-changes`      | El backend acepta `devolver_a` (`design`, `build`, `qa`); el portal no lo envía, así que el Gate 3 siempre devuelve a QA.                                                                                                 | **Pendiente** → KC-PT-06-7.                                                                                        |
| D7  | `GET /dashboard/kanban`                          | El backend devuelve también la columna `produccion` (20 más recientes); el portal pinta solo 6 columnas y esos tickets no se ven.                                                                                         | **Pendiente**: decisión de producto (¿se muestra la columna?).                                                     |
| D8  | `GET /audit/tickets/{n}`                         | §11.2 define `cambios[]`; el backend aún envía `eventos[]`. El portal muestra ambos (KC-PT-01-4).                                                                                                                         | **Pendiente** backend → KC-BE-08-6.                                                                                |
| D9  | `GET /usage/by-ticket`                           | `total` cuenta todos los tickets del tenant, pero `items` filtra por `creado_en <= ciclo`: en ciclos pasados la paginación no cuadra.                                                                                     | **Pendiente** backend.                                                                                             |
| D10 | `POST /auth/login`                               | Desde KC-BE-02-3, si el correo pertenece a varias organizaciones el backend responde 409 `AUTH_MULTIPLE_TENANTS` y acepta `org_id`. El portal no envía `org_id`, y el 409 no trae la lista de organizaciones para elegir. | **Pendiente**: el backend debe exponer las organizaciones (p. ej. en `detalle`) para que el portal ofrezca elegir. |

Revisado y **sin divergencia**: el sobre de error `{error:{codigo,mensaje,detalle,trace_id,timestamp}}`
que convierte `client.ts`; las cabeceras CORS (`Authorization`, `Content-Type`, `X-Trace-Id`); y los
identificadores de ticket, que en todas las respuestas son el número `KC-####`.

## 4. Inventario por endpoint

Columna **Campos**: los que el portal lee de la respuesta (o envía, en las escrituras).
Los campos que el backend envía y el portal no usa no se listan.

| Método | Ruta `/api/v1/…`                        | Vista / módulo           | Campos                                                                                                                                                            | Estado |
| ------ | --------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| POST   | `auth/login`                            | `estado/auth.tsx`        | envía `email, password` · lee `access_token, refresh_token, usuario{id,nombre,email,rol,idioma,org_id}, acceso_restringido`                                       | D10    |
| POST   | `auth/refresh`                          | `api/client.ts`          | envía `refresh_token` · lee `access_token, refresh_token`                                                                                                         | OK     |
| POST   | `auth/logout`                           | `api/client.ts`          | envía `Authorization` + `refresh_token`                                                                                                                           | OK     |
| GET    | `users/me`                              | `estado/auth.tsx`        | `id, nombre, email, rol, idioma` (al iniciar sesión y tras cada renovación)                                                                                       | OK     |
| GET    | `organization`                          | Conexiones               | `fabrica_activa`                                                                                                                                                  | OK     |
| GET    | `dashboard/summary`                     | Tablero                  | `en_cola, en_proceso, esperando_aprobacion, en_produccion_30d`                                                                                                    | OK     |
| GET    | `dashboard/lines`                       | Tablero                  | `lineas[]{numero, ticket_id, etapa}`                                                                                                                              | D1     |
| GET    | `dashboard/kanban`                      | Tablero                  | `columnas{etapa: [{id, titulo, gate_pendiente}]}`                                                                                                                 | D7     |
| GET    | `tickets`                               | Tickets                  | query `page, page_size, q, etapa, prioridad` · lee `items[]{id,titulo,tipo,prioridad,etapa,gate_pendiente,origen,actualizado_en}, total`                          | OK     |
| POST   | `tickets`                               | NuevoTicket              | envía `tipo, titulo, descripcion, prioridad, modulo?` · lee `id, etapa`                                                                                           | D3     |
| POST   | `tickets/features`                      | NuevaFuncionalidad       | envía `nombre, objetivo, criterios[]{given,when,then}` · lee `id`                                                                                                 | OK     |
| POST   | `tickets/dedup-preview`                 | NuevoTicket              | envía `texto, k` · lee `similares[]{ticket_id, titulo, similitud}`                                                                                                | D2     |
| POST   | `tickets/impact-preview`                | NuevaFuncionalidad       | envía `texto` · lee `modulos, tablas, confianza`                                                                                                                  | D2     |
| GET    | `tickets/{n}`                           | DetalleTicket            | `id, titulo, tipo, prioridad, origen, reportado_por{nombre}, etapa, gate_pendiente, pipeline[]{etapa,estado}, detalle{linea,pr_url}, visualizadores[]{id,nombre}` | OK     |
| PATCH  | `tickets/{n}`                           | DetalleTicket (cancelar) | envía `accion: "cancelar", motivo_cancelacion`                                                                                                                    | OK     |
| GET    | `tickets/{n}/spec`                      | DetalleTicket            | `version, estado, alternativas[]{id,titulo,descripcion,recomendada,elegida}, criterios[]{id,given,when,then}, scope_paths`                                        | OK     |
| GET    | `tickets/{n}/evidence`                  | DetalleTicket            | `matriz[]{criterio_id, resultado, extracto_fallo}, regresion{dirigidos, dirigidos_ok, suite_cliente, cliente_ok}`                                                 | OK     |
| GET    | `tickets/{n}/security`                  | DetalleTicket            | `hallazgos[]{id, severidad, titulo, estado, justificacion}, bloquea_deploy`                                                                                       | OK     |
| GET    | `tickets/{n}/usage`                     | DetalleTicket            | `totales{tokens_in,tokens_out,invocaciones,loc_netas,loc_tests}, por_funcion[]{funcion,modelo,invocaciones,tokens_in,tokens_out}`                                 | OK     |
| GET    | `tickets/{n}/activity`                  | DetalleTicket            | query `page_size` · lee `items[]{fecha, actor{tipo,nombre}, evento}`                                                                                              | OK     |
| POST   | `tickets/{n}/gates/{g}/approve`         | DetalleTicket            | envía `comentario?, alternativa_id?`                                                                                                                              | D5     |
| POST   | `tickets/{n}/gates/{g}/request-changes` | DetalleTicket            | envía `comentario`                                                                                                                                                | D6     |
| GET    | `approvals/pending`                     | Aprobaciones, Layout     | `items[]{ticket_id, titulo, gate, que_se_aprueba, esperando_desde}`                                                                                               | OK     |
| GET    | `notifications`                         | Layout (badge)           | query `page_size=1` · lee `no_leidas`                                                                                                                             | OK     |
| GET    | `audit/events`                          | Auditoría                | query `page, page_size, actor_tipo, q` · lee `items[]{id,fecha,ticket_id,actor_tipo,actor_id,evento,modelo,sello}, total, cadena{eventos_hoy}`                    | OK     |
| GET    | `audit/tickets/{n}`                     | Expediente               | `solicitante, integridad{integra,hash_raiz}, firmas[], etapas[]{etapa,pasos[]}, cambios?[], eventos?[]`                                                           | D8     |
| GET    | `connections`                           | Conexiones               | `conexiones[]{id,categoria,proveedor,nombre,estado,ultimo_test}, mapa_ramas{feature,staging,produccion}`                                                          | OK     |
| POST   | `connections/{id}/test`                 | Conexiones               | lee `estado`                                                                                                                                                      | OK     |
| POST   | `onboarding/complete`                   | Conexiones               | lee `lineas_activadas`                                                                                                                                            | OK     |
| GET    | `models/providers`                      | ModelosIA                | `tiers[]{tier, modelo, nombre, perfil}`                                                                                                                           | OK     |
| GET    | `models/assignments`                    | ModelosIA                | `politica_escalado, asignaciones[]{paso,etapa,tier,modelo,escalable,descripcion}`                                                                                 | OK     |
| GET    | `usage/summary`                         | Consumo                  | `ciclo, plan{codigo,loc_mes,renueva_el}, loc{consumidas,restantes,porcentaje}, tickets{cerrados,en_proceso}, umbral`                                              | OK     |
| GET    | `usage/by-ticket`                       | Consumo                  | `items[]{ticket_id,titulo,tipo,loc_netas,tokens,etapa}`                                                                                                           | D9     |
| POST   | `plans/upgrade-request`                 | Consumo                  | envía `plan_deseado` · lee `mensaje`                                                                                                                              | OK     |

## 5. Operaciones sin consumidor en el portal

| Operación                                                                                      | Ticket que la incorporará                                               |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `POST auth/forgot-password`, `auth/reset-password`, `auth/invitations/accept`                  | KC-PT-10-6                                                              |
| `POST tickets/attachments`, `GET tickets/{n}/attachments/{id}`                                 | KC-PT-05-1 / KC-PT-05-2                                                 |
| `GET tickets/{n}/gates`                                                                        | KC-PT-06-4                                                              |
| `GET onboarding/codemapping/status`                                                            | KC-PT-07-5                                                              |
| `PATCH users/me`, `GET users`, `POST users/invitations`, `PATCH users/{id}`                    | Perfil y equipo (EPT4)                                                  |
| `PATCH organization`                                                                           | Sin ticket identificado                                                 |
| `POST`/`DELETE tickets/{n}/watchers`                                                           | Sin ticket identificado                                                 |
| `GET audit/steps/{id}`, `POST audit/exports`, `GET audit/exports/{id}`, `GET audit/integrity`  | Sin ticket identificado                                                 |
| `PATCH notifications/{id}`, `POST notifications/read-all`                                      | Bandeja de notificaciones (EPT4)                                        |
| `POST`/`PATCH`/`DELETE connections`, `PUT connections/branch-map`, `POST onboarding/documents` | Asistente de onboarding (EPT4)                                          |
| `GET plans`                                                                                    | Comparador de planes (EPT4)                                             |
| `PUT models/providers/{proveedor}`                                                             | No aplica: siempre 403 en modalidad gestionada (decisión de diseño D10) |

## 6. Cómo mantener este inventario

Mientras el backend no publique `response_model` en `/api/v1`:

1. Si un PR del portal empieza a leer un campo nuevo, actualizar la fila del endpoint aquí,
   el tipo en `src/api/tipos.ts` y el dato de prueba en `src/mocks/datos.ts`.
2. Si un PR del backend cambia una respuesta que aparece en la sección 4, avisar al equipo
   del portal (Equipo 4).

Cuando exista el contrato tipado, KC-PT-04-2 reemplaza este documento por tipos generados y
validación Zod en la frontera.
