# Matriz de interacción del portal (selectores v1)

> Generado desde `contratos/selectores-portal-v1.json` por `src/selectores.test.ts`. No se edita a mano: se cambia el JSON y se corre `npm run selectores:doc`.

Contrato de selectores `data-testid` del portal con el equipo de Testing (KC-PT-05-5): **332 selectores en 17 vistas**.

## Cómo usarlo

- Cada selector es un `data-testid` con la convención `vista.zona.elemento`: `page.getByTestId("tickets.filtros.busqueda")` en Playwright.
- `vista` es la pantalla (el archivo de la vista en kebab-case, o su carpeta para los componentes de esa vista); `layout`, `comun` y `error` son compartidos y aparecen dentro de cualquier vista: conviene buscarlos dentro de `<vista>.vista.raiz`.
- Los modales (tipo modal) se abren por encima de la vista: mientras están abiertos, el foco queda dentro de ellos.
- Los selectores marcados con 🔁 se repiten una vez por fila o ítem: se distinguen por texto (`.filter({ hasText })`) o posición (`.nth(i)`). No llevan ids de datos.
- **Tipo** usa los mismos valores que `SelectorKind` de `packages/testing` (inventory_client.py): button, input, table, card, link y modal.
- **Cuándo aparece** indica el rol, el estado o los datos que necesita el selector; si está vacío, aparece siempre que la vista cargó.

| Acción | Qué hacer |
| --- | --- |
| click | Hacer clic |
| escribir | Escribir texto |
| seleccionar | Elegir una opción |
| subir | Adjuntar archivos (setInputFiles) |
| soltar | Soltar archivos (drag & drop) |
| verificar | Solo verificar (visible, texto, estado) |

## Reglas de cambio

- La versión 1 está congelada: un selector no se renombra ni se quita sin anunciarlo en `contratos/cambios-anunciados.yaml` (`breaking: true`, con aprobación de Testing) y publicar una versión nueva del contrato.
- Agregar selectores no rompe a nadie: se agregan al JSON y se anuncian con `breaking: false`.
- El test `src/selectores.test.ts` del portal falla si el código y el contrato no coinciden, en cualquiera de los dos sentidos.

## Vistas

| Vista | Ruta | Roles | Selectores |
| --- | --- | --- | --- |
| [layout](#layout) | `*` | owner, admin, architect, approver, member, viewer | 19 |
| [bandeja-notificaciones](#bandeja-notificaciones) | `*` | owner, admin, architect, approver, member, viewer | 6 |
| [comun](#comun) | `*` | owner, admin, architect, approver, member, viewer | 2 |
| [error](#error) | `*` | owner, admin, architect, approver, member, viewer | 6 |
| [login](#login) | `/login` | sin sesión | 4 |
| [tablero](#tablero) | `/` | owner, admin, architect, approver, member, viewer | 17 |
| [tickets](#tickets) | `/tickets` | owner, admin, architect, approver, member, viewer | 13 |
| [detalle-ticket](#detalle-ticket) | `/tickets/:numero` | owner, admin, architect, approver, member, viewer | 82 |
| [nuevo-ticket](#nuevo-ticket) | `/nuevo-ticket` | owner, admin, architect, approver, member | 27 |
| [nueva-funcionalidad](#nueva-funcionalidad) | `/nueva-funcionalidad` | owner, admin, architect, approver, member | 34 |
| [aprobaciones](#aprobaciones) | `/aprobaciones` | owner, admin, architect, approver | 4 |
| [auditoria](#auditoria) | `/auditoria` | owner, admin, architect | 10 |
| [expediente](#expediente) | `/auditoria/:numero` | owner, admin, architect, approver | 9 |
| [onboarding](#onboarding) | `/onboarding` | owner, admin | 65 |
| [conexiones](#conexiones) | `/conexiones` | owner, admin | 14 |
| [modelos-ia](#modelos-ia) | `/modelos` | owner, admin, architect | 5 |
| [consumo](#consumo) | `/consumo` | owner, admin, architect, member | 15 |

## layout

Header y navegación presentes en todas las vistas con sesión. Ruta `*` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `layout.cabecera.idioma` | input | seleccionar | Selector de idioma (es, en, pt): cambia todos los textos. |  |
| `layout.cabecera.tema` | button | click | Alterna tema claro y oscuro; se recuerda al recargar. |  |
| `layout.cabecera.campana` | button | click | Campana: abre y cierra la bandeja de notificaciones (aria-expanded). Su nombre accesible incluye cuántas hay sin leer. | si la suscripción no está suspendida |
| `layout.cabecera.notificaciones` | card | verificar | Contador de notificaciones sin leer, dentro de layout.cabecera.campana. | solo si hay sin leer y la suscripción no está suspendida |
| `layout.cabecera.avatar` | card | verificar | Iniciales del usuario; el nombre completo va en el atributo title. |  |
| `layout.cabecera.salir` | button | click | Cierra la sesión y vuelve a /login. |  |
| `layout.nav.tablero` | link | click | Abre el Tablero (/). | si el rol tiene acceso a la ruta |
| `layout.nav.tickets` | link | click | Abre Tickets (/tickets). | si el rol tiene acceso a la ruta |
| `layout.nav.nuevo-ticket` | link | click | Abre Nuevo ticket (/nuevo-ticket). | si el rol tiene acceso a la ruta |
| `layout.nav.nueva-funcionalidad` | link | click | Abre Nueva funcionalidad (/nueva-funcionalidad). | si el rol tiene acceso a la ruta |
| `layout.nav.aprobaciones` | link | click | Abre Aprobaciones (/aprobaciones). | si el rol tiene acceso a la ruta |
| `layout.nav.pendientes` | card | verificar | Cantidad de gates pendientes junto a Aprobaciones. | si hay gates pendientes |
| `layout.nav.auditoria` | link | click | Abre Auditoría (/auditoria). | si el rol tiene acceso a la ruta |
| `layout.nav.onboarding` | link | click | Enlace al asistente de alta (/onboarding). | si el rol tiene acceso a la ruta |
| `layout.nav.conexiones` | link | click | Abre Conexiones (/conexiones). | si el rol tiene acceso a la ruta |
| `layout.nav.modelos-ia` | link | click | Abre Modelos IA (/modelos). | si el rol tiene acceso a la ruta |
| `layout.nav.consumo` | link | click | Abre Consumo y plan (/consumo). | si el rol tiene acceso a la ruta |
| `layout.avisos.suscripcion` | card | verificar | Aviso de suscripción suspendida: solo queda disponible Consumo y plan. | suscripción suspendida |
| `layout.avisos.acceso` | card | verificar | Aviso de redirección: el rol no tiene acceso a la ruta pedida. | después de una redirección por rol, hasta la siguiente navegación |

## bandeja-notificaciones

Bandeja desplegable de notificaciones (KC-PT-06-1). Se abre con layout.cabecera.campana y se cierra con Escape, con un clic afuera o al abrir una notificación. Ruta `*` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `bandeja-notificaciones.panel.contenedor` | card | verificar | Panel de la bandeja con las 20 notificaciones más recientes. | con la bandeja abierta |
| `bandeja-notificaciones.cabecera.leer-todas` | button | click | Marca todas las notificaciones como leídas; el contador de la campana vuelve a 0. | deshabilitado si no hay notificaciones sin leer |
| `bandeja-notificaciones.lista.item` 🔁 | table | verificar | Notificación: ícono por tipo, título, cuerpo, fecha y la etiqueta «Nueva» si no está leída (clase no-leida). |  |
| `bandeja-notificaciones.lista.abrir` 🔁 | link | click | Abre el ticket de la notificación, la marca como leída y cierra la bandeja. | si el backend envía ticket_numero |
| `bandeja-notificaciones.lista.alternar` 🔁 | button | click | Marca la notificación como leída o como no leída (aria-label con el título). |  |
| `bandeja-notificaciones.lista.vacio` | card | verificar | «No tienes notificaciones.» | si no hay notificaciones |

## comun

Componentes compartidos que aparecen dentro de cualquier vista. Buscarlos dentro de <vista>.vista.raiz. Ruta `*` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `comun.error.caja` | card | verificar | Error del backend o de red: código, mensaje traducido y trace_id. |  |
| `comun.carga.indicador` | card | verificar | Texto «Cargando…» mientras llega la respuesta. |  |

## error

Pantallas de recuperación cuando falla el render (LimiteError). Ruta `*` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `error.ruta.pantalla` | card | verificar | Recuperación de una vista que falló; el header y la navegación siguen visibles. | error de render en una vista |
| `error.ruta.reintentar` | button | click | Vuelve a intentar mostrar la vista. |  |
| `error.ruta.inicio` | link | click | Va al inicio (/). |  |
| `error.raiz.pantalla` | card | verificar | Recuperación a pantalla completa. | error de render fuera de las vistas |
| `error.raiz.reintentar` | button | click | Vuelve a intentar mostrar el portal. |  |
| `error.raiz.inicio` | link | click | Va al inicio (/). |  |

## login

Inicio de sesión (sin sesión). Ruta `/login` · roles: sin sesión.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `login.vista.raiz` | card | verificar | Contenedor del login. |  |
| `login.formulario.correo` | input | escribir | Correo del usuario. |  |
| `login.formulario.clave` | input | escribir | Contraseña (mínimo 8 caracteres). |  |
| `login.formulario.entrar` | button | click | Inicia sesión: va a la ruta pedida o al Tablero; con credenciales inválidas muestra comun.error.caja. |  |

## tablero

KPIs, líneas de producción y kanban; se refresca solo según el polling de la organización. Ruta `/` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `tablero.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `tablero.carga.reintentar` | button | click | Reintenta la carga del tablero. | si falló la carga inicial |
| `tablero.cabecera.nuevo-ticket` | button | click | Abre Nuevo ticket. |  |
| `tablero.refresco.aviso` | card | verificar | Aviso de refresco fallido con la hora de la última actualización correcta; los datos anteriores se mantienen. | si falló un refresco |
| `tablero.kpis.en-cola` | card | verificar | KPI: tickets en cola (Triage). |  |
| `tablero.kpis.en-proceso` | card | verificar | KPI: tickets en proceso. |  |
| `tablero.kpis.esperando-aprobacion` | card | verificar | KPI: tickets esperando aprobación. |  |
| `tablero.kpis.en-produccion` | card | verificar | KPI: tickets en producción en los últimos 30 días. |  |
| `tablero.lineas.tabla` | table | verificar | Tabla de líneas de producción. |  |
| `tablero.lineas.fila` 🔁 | table | click | Línea de producción; si tiene un ticket asignado, abre su detalle. |  |
| `tablero.kanban.col-triage` | card | verificar | Columna del kanban para la etapa «triage», con su contador. |  |
| `tablero.kanban.col-design` | card | verificar | Columna del kanban para la etapa «design», con su contador. |  |
| `tablero.kanban.col-build` | card | verificar | Columna del kanban para la etapa «build», con su contador. |  |
| `tablero.kanban.col-qa` | card | verificar | Columna del kanban para la etapa «qa», con su contador. |  |
| `tablero.kanban.col-security` | card | verificar | Columna del kanban para la etapa «security», con su contador. |  |
| `tablero.kanban.col-deploy` | card | verificar | Columna del kanban para la etapa «deploy», con su contador. |  |
| `tablero.kanban.tarjeta` 🔁 | link | click | Ticket en el kanban; borde punteado si espera un gate. Abre su detalle. |  |

## tickets

Listado de tickets con búsqueda, filtros y paginación en la URL. Ruta `/tickets` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `tickets.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `tickets.cabecera.nueva-funcionalidad` | button | click | Abre Nueva funcionalidad. |  |
| `tickets.cabecera.nuevo-ticket` | button | click | Abre Nuevo ticket. |  |
| `tickets.filtros.busqueda` | input | escribir | Búsqueda por texto: consulta al dejar de escribir (350 ms) y vuelve a la página 1. |  |
| `tickets.filtros.etapa` | input | seleccionar | Filtro por etapa; vuelve a la página 1. |  |
| `tickets.filtros.prioridad` | input | seleccionar | Filtro por prioridad (alta, media, baja); vuelve a la página 1. |  |
| `tickets.lista.tabla` | table | verificar | Tabla de tickets. |  |
| `tickets.lista.fila` 🔁 | table | click | Ticket del listado; abre su detalle. |  |
| `tickets.lista.vacio` | table | verificar | Fila «Sin resultados». | si la búsqueda o los filtros no devuelven tickets |
| `tickets.paginacion.contenedor` | card | verificar | Paginación del listado. | solo si hay más de una página |
| `tickets.paginacion.anterior` | button | click | Va a la página anterior. | deshabilitado en la página 1 |
| `tickets.paginacion.estado` | card | verificar | Texto «Página N de M». |  |
| `tickets.paginacion.siguiente` | button | click | Va a la página siguiente; la página queda en la URL (?page=N). | deshabilitado en la última página |

## detalle-ticket

Detalle de un ticket: pipeline, 5 pestañas, panel de gate y acciones. Se refresca cada 20 s. Ruta `/tickets/:numero` · roles: owner, admin, architect, approver, member, viewer.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `detalle-ticket.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `detalle-ticket.cabecera.volver` | link | click | Vuelve a Tickets. |  |
| `detalle-ticket.cabecera.origen` | card | verificar | Origen del ticket con su nombre (Portal, Jira, GitHub, Kallicode Help o CoreStream); un origen desconocido, tal cual. |  |
| `detalle-ticket.pipeline.etapa-triage` | card | verificar | Etapa «triage» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-design` | card | verificar | Etapa «design» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-build` | card | verificar | Etapa «build» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-qa` | card | verificar | Etapa «qa» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-security` | card | verificar | Etapa «security» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-deploy` | card | verificar | Etapa «deploy» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pipeline.etapa-produccion` | card | verificar | Etapa «produccion» del pipeline; clase done si terminó, now si es la actual. |  |
| `detalle-ticket.pestanas.spec` | link | click | Abre la pestaña Spec. |  |
| `detalle-ticket.pestanas.evidencia` | link | click | Abre la pestaña Evidencia QA. |  |
| `detalle-ticket.pestanas.seguridad` | link | click | Abre la pestaña Seguridad. |  |
| `detalle-ticket.pestanas.consumo` | link | click | Abre la pestaña Consumo. |  |
| `detalle-ticket.pestanas.actividad` | link | click | Abre la pestaña Actividad. |  |
| `detalle-ticket.spec.contenido` | card | verificar | Spec: alternativas, criterios Given/When/Then y alcance autorizado. |  |
| `detalle-ticket.spec.sin-datos` | card | verificar | Pestaña Spec sin datos («—»). | el backend responde 404: aún no hay spec |
| `detalle-ticket.evidencia.tabla` | table | verificar | Matriz de evidencia QA por criterio (pasa, falla, flaky). |  |
| `detalle-ticket.evidencia.sin-datos` | card | verificar | Pestaña Evidencia QA sin datos («—»). | el backend responde 404: aún no hay corridas de QA |
| `detalle-ticket.evidencia.galeria` | card | verificar | Galería de capturas, videos y trazas de la corrida, agrupada por criterio. |  |
| `detalle-ticket.evidencia.galeria-vacia` | card | verificar | La corrida no guardó capturas, videos ni trazas. | ningún criterio trae evidencias |
| `detalle-ticket.evidencia.item` | card | verificar | Una evidencia (captura, video, traza u otro archivo); se repite por evidencia y se distingue por su texto. |  |
| `detalle-ticket.evidencia.miniatura` | link | click | Miniatura de la captura; abre la imagen en una pestaña nueva. | captura con enlace de descarga |
| `detalle-ticket.evidencia.ver-video` | button | click | Abre el video en el diálogo detalle-ticket.video.dialogo. | video con enlace de descarga |
| `detalle-ticket.evidencia.descargar` | link | click | Descarga la evidencia con su enlace firmado (se abre en una pestaña nueva). | la evidencia trae download_url |
| `detalle-ticket.evidencia.no-disponible` | card | verificar | La evidencia aún no tiene enlace de descarga. | el backend no envía download_url (pendiente de KC-BE-08-5) |
| `detalle-ticket.evidencia.renovando` | card | verificar | Aviso: el enlace venció y se pidió uno nuevo. | se usó un enlace con expira_en vencido, hasta recibir la respuesta nueva |
| `detalle-ticket.video.dialogo` | modal | verificar | Diálogo con el video de la evidencia (role=dialog): el foco entra al abrir, queda atrapado con Tab y Escape lo cierra. | después de detalle-ticket.evidencia.ver-video |
| `detalle-ticket.video.reproductor` | card | verificar | Reproductor del video con controles. |  |
| `detalle-ticket.video.descargar` | link | click | Descarga el video que se está viendo. |  |
| `detalle-ticket.video.cerrar` | button | click | Cierra el diálogo del video; el foco vuelve a detalle-ticket.evidencia.ver-video. |  |
| `detalle-ticket.seguridad.tabla` | table | verificar | Hallazgos de seguridad con severidad y estado. |  |
| `detalle-ticket.seguridad.bloqueo` | card | verificar | Aviso: severidad alta sin resolver bloquea el Deploy. | si hay un hallazgo que bloquea el deploy |
| `detalle-ticket.seguridad.sin-datos` | card | verificar | Pestaña Seguridad sin datos («—»). | el backend responde 404 |
| `detalle-ticket.consumo.tabla` | table | verificar | Consumo del ticket por función y modelo. |  |
| `detalle-ticket.consumo.sin-datos` | card | verificar | Pestaña Consumo sin datos («—»). | el backend responde 404 |
| `detalle-ticket.actividad.lista` | table | verificar | Actividad del ticket: fecha, actor y evento. |  |
| `detalle-ticket.actividad.sin-datos` | card | verificar | Pestaña Actividad sin datos («—»). | el backend responde 404 |
| `detalle-ticket.paginacion.contenedor` | card | verificar | Paginación del listado. | pestaña Actividad; solo si hay más de una página |
| `detalle-ticket.paginacion.anterior` | button | click | Va a la página anterior. | pestaña Actividad; deshabilitado en la página 1 |
| `detalle-ticket.paginacion.estado` | card | verificar | Texto «Página N» (el backend no informa el total). | pestaña Actividad |
| `detalle-ticket.paginacion.siguiente` | button | click | Va a la página siguiente; la página queda en la URL (?actividad=N). | pestaña Actividad; deshabilitado si la página no vino llena |
| `detalle-ticket.resumen.impacto-pendiente` | card | verificar | El impacto aún no está calculado. | el triage no corrió (detalle.radio_impacto null) |
| `detalle-ticket.resumen.impacto-radio` | card | verificar | Radio de impacto del triage: Bajo, Medio o Alto (con el color de la prioridad). | el triage calculó el radio |
| `detalle-ticket.resumen.impacto-riesgo` | card | verificar | Riesgo de regresión del triage, en porcentaje. | el triage calculó el riesgo |
| `detalle-ticket.resumen.impacto-modulos` | card | verificar | Módulos afectados según el triage (lista). | el triage encontró módulos afectados |
| `detalle-ticket.resumen.pr` | link | click | Abre el PR del ticket en una pestaña nueva. | si el ticket tiene PR con URL http(s) |
| `detalle-ticket.visualizadores.lista` | card | verificar | Visualizadores del ticket (lista): nombre, avatar y marca del creador y de quien mira. |  |
| `detalle-ticket.visualizadores.item` | card | verificar | Un visualizador; se repite por persona y se distingue por su nombre. |  |
| `detalle-ticket.visualizadores.quitar` | button | click | Quita a ese visualizador (DELETE /tickets/{numero}/watchers/{user_id}); el foco pasa a la lista. | owner, admin o el creador del ticket; nunca sobre el creador ni sobre uno mismo |
| `detalle-ticket.visualizadores.seguir` | button | click | Se agrega a uno mismo como visualizador; el mismo botón pasa a dejar-de-seguir. | no sigue el ticket, no es viewer y hay cupo |
| `detalle-ticket.visualizadores.dejar-de-seguir` | button | click | Se quita a uno mismo; el mismo botón pasa a seguir. | sigue el ticket y no es su creador |
| `detalle-ticket.visualizadores.agregar` | button | click | Abre o cierra el buscador de personas (aria-expanded). | owner, admin, architect o approver, y hay cupo |
| `detalle-ticket.visualizadores.buscador` | card | verificar | Buscador de personas activas de la organización (GET /users). | después de detalle-ticket.visualizadores.agregar |
| `detalle-ticket.visualizadores.buscar` | input | escribir | Nombre o correo a buscar; se consulta al dejar de escribir. |  |
| `detalle-ticket.visualizadores.resultado` | button | click | Agrega a esa persona (POST /tickets/{numero}/watchers); se repite por resultado y excluye a quienes ya son visualizadores. El foco vuelve al buscador. |  |
| `detalle-ticket.visualizadores.sin-resultados` | card | verificar | No hay más personas que coincidan. | la búsqueda no deja candidatos |
| `detalle-ticket.visualizadores.limite` | card | verificar | El ticket ya tiene el máximo de 25 visualizadores; no se ofrece agregar ni seguir. | 25 visualizadores |
| `detalle-ticket.gate.panel` | card | verificar | Panel para firmar o devolver el gate pendiente. | si hay un gate pendiente y el rol puede firmarlo: Gate 1 y 2 owner, admin, architect o approver; Gate 3 solo architect |
| `detalle-ticket.gate.alternativa` | input | seleccionar | Alternativa de diseño a aprobar; propone la recomendada. | Gate 1 con más de una alternativa |
| `detalle-ticket.gate.comentario` | input | escribir | Comentario de la decisión; vuelve como contexto al pipeline. |  |
| `detalle-ticket.gate.aprobar` | button | click | Abre el modal de firma (detalle-ticket.firma.dialogo) para aprobar el gate; la firma se confirma ahí. |  |
| `detalle-ticket.gate.pedir-cambios` | button | click | Abre el modal de firma (detalle-ticket.firma.dialogo) para devolver el ticket con cambios. | habilitado con un comentario de 10 caracteres o más |
| `detalle-ticket.firma.dialogo` | modal | verificar | Modal de firma (role=dialog): el foco entra al abrir, queda atrapado con Tab y Escape lo cierra salvo mientras se envía. | después de detalle-ticket.gate.aprobar o pedir-cambios |
| `detalle-ticket.firma.resumen` | card | verificar | Qué se firma: ticket, gate, alternativa elegida (Gate 1) y comentario. | antes de confirmar |
| `detalle-ticket.firma.cancelar` | button | click | Cierra el modal sin decidir; el foco vuelve al botón que lo abrió. | antes de confirmar; deshabilitado mientras se envía |
| `detalle-ticket.firma.confirmar` | button | click | Envía la decisión: firma el gate (approve) o pide cambios (request-changes). | antes de confirmar; deshabilitado mientras se envía |
| `detalle-ticket.firma.resultado` | card | verificar | Resultado de la decisión: «GATE N firmado.» o la etapa a la que vuelve el ticket con su iteración. | después de confirmar |
| `detalle-ticket.firma.sello` | card | verificar | Sello de la firma en la cadena de auditoría. | después de firmar (approve) |
| `detalle-ticket.firma.aviso-cuota` | card | verificar | Aviso del backend: la estimación de LOC de la spec supera la cuota restante del ciclo. | después de firmar el Gate 1, si el backend envía aviso_cuota |
| `detalle-ticket.firma.cerrar` | button | click | Cierra el modal después de decidir; el foco vuelve al contenido principal. | después de confirmar |
| `detalle-ticket.firmas.cadena` | card | verificar | Cadena de firmas de los gates 1, 2 y 3, en orden (GET /tickets/{numero}/gates). |  |
| `detalle-ticket.firmas.gate` | card | verificar | Un gate de la cadena; se repite por gate en orden (1, 2, 3). El que espera firma lleva aria-current=step. |  |
| `detalle-ticket.firmas.estado` | card | verificar | Estado del gate: Firmado, Esperando firma o Aún no se alcanza. |  |
| `detalle-ticket.firmas.sello` | card | verificar | Sello completo de la firma en la cadena de auditoría (la vista lo recorta con «…»). | el gate está firmado |
| `detalle-ticket.firmas.expediente` | link | click | Abre el expediente del ticket, donde se verifica la cadena de hashes. | hay al menos un gate firmado y el rol puede ver /auditoria/:numero (admin, owner, architect, approver) |
| `detalle-ticket.acciones.cancelar` | button | click | Abre el modal de cancelación (detalle-ticket.cancelacion.dialogo). | creador del ticket, owner o admin; no en deploy, producción ni cancelado |
| `detalle-ticket.cancelacion.dialogo` | modal | verificar | Modal de cancelación (role=dialog): avisa que no se puede deshacer; el foco entra al motivo, queda atrapado con Tab y Escape lo cierra salvo mientras se envía. | después de detalle-ticket.acciones.cancelar |
| `detalle-ticket.cancelacion.motivo` | input | escribir | Motivo de la cancelación (5 a 500 caracteres); queda en la auditoría del ticket. |  |
| `detalle-ticket.cancelacion.error` | card | verificar | Error del motivo, del portal o del 422 del backend. | el motivo no cumple la regla al confirmar, o el backend lo rechaza |
| `detalle-ticket.cancelacion.volver` | button | click | Cierra el modal sin cancelar; el foco vuelve a detalle-ticket.acciones.cancelar. |  |
| `detalle-ticket.cancelacion.confirmar` | button | click | Cancela el ticket (PATCH accion=cancelar) y vuelve a la lista de tickets. | el motivo es válido |

## nuevo-ticket

Alta de bug o mejora con duplicados en vivo y evidencia adjunta. Ruta `/nuevo-ticket` · roles: owner, admin, architect, approver, member.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `nuevo-ticket.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `nuevo-ticket.formulario.tipo-bug` | button | click | Marca el ticket como bug (opción por defecto). |  |
| `nuevo-ticket.formulario.tipo-mejora` | button | click | Marca el ticket como mejora. |  |
| `nuevo-ticket.formulario.tipo-funcionalidad` | button | click | Lleva a Nueva funcionalidad. |  |
| `nuevo-ticket.formulario.titulo` | input | escribir | Título (5 a 200 caracteres). |  |
| `nuevo-ticket.formulario.descripcion` | input | escribir | Descripción (10 caracteres o más); con 10+ caracteres entre título y descripción consulta duplicados. |  |
| `nuevo-ticket.formulario.prioridad` | input | seleccionar | Prioridad: alta, media (por defecto) o baja. |  |
| `nuevo-ticket.formulario.modulo` | input | escribir | Módulo o pantalla afectada (opcional). |  |
| `nuevo-ticket.formulario.aviso` | card | verificar | Aviso de cuota mensual agotada: el ticket quedó en cola; luego abre su detalle. | ticket creado con la cuota agotada |
| `nuevo-ticket.formulario.cancelar` | button | click | Vuelve a Tickets sin crear el ticket. |  |
| `nuevo-ticket.formulario.enviar` | button | click | Crea el ticket con los adjuntos ya subidos y abre su detalle. | deshabilitado mientras se envía o hay adjuntos subiendo |
| `nuevo-ticket.errores.resumen` | card | verificar | Aviso «Revisa los campos marcados» (role=alert); lista los errores de campos que el formulario no muestra. | al enviar con errores, o tras un 422 VALIDACION_ENTRADA |
| `nuevo-ticket.errores.titulo` | card | verificar | Error del campo titulo (5 a 200 caracteres), del portal o del 422 del backend; el control queda con aria-invalid. | el campo no cumple su regla al enviar, o el backend lo rechaza |
| `nuevo-ticket.errores.descripcion` | card | verificar | Error del campo descripcion (10 a 10.000 caracteres), del portal o del 422 del backend; el control queda con aria-invalid. | el campo no cumple su regla al enviar, o el backend lo rechaza |
| `nuevo-ticket.errores.modulo` | card | verificar | Error del campo modulo (hasta 200 caracteres), del portal o del 422 del backend; el control queda con aria-invalid. | el campo no cumple su regla al enviar, o el backend lo rechaza |
| `nuevo-ticket.similares.vacio` | card | verificar | Sin posibles duplicados («—»). |  |
| `nuevo-ticket.similares.tarjeta` 🔁 | link | click | Posible duplicado con su % de similitud; abre su detalle. | si el backend encuentra tickets parecidos |
| `nuevo-ticket.adjuntos.zona` | card | soltar | Zona de arrastre de archivos. Pegar una captura en cualquier parte del formulario también la adjunta. |  |
| `nuevo-ticket.adjuntos.elegir` | button | click | Abre el selector de archivos del sistema. |  |
| `nuevo-ticket.adjuntos.entrada` | input | subir | input type=file oculto: usar setInputFiles. Acepta imágenes, video, PDF, DOCX, MD, TXT, CSV y ZIP. |  |
| `nuevo-ticket.adjuntos.archivo` 🔁 | table | verificar | Archivo agregado: nombre, tamaño y estado. |  |
| `nuevo-ticket.adjuntos.progreso` 🔁 | card | verificar | Barra de progreso de la subida (aria-label «Subida de <nombre>»). | mientras el archivo se sube |
| `nuevo-ticket.adjuntos.error` 🔁 | card | verificar | Motivo del rechazo o del fallo: tipo no permitido, vacío, más de 25 MB, más de 10 archivos o cuota de almacenamiento agotada (con trace_id si viene del backend). | archivo rechazado o con error |
| `nuevo-ticket.adjuntos.reintentar` 🔁 | button | click | Vuelve a subir el archivo. | solo en fallos de red o del almacenamiento |
| `nuevo-ticket.adjuntos.quitar` 🔁 | button | click | Quita el archivo de la lista y cancela su subida si está en curso. |  |
| `nuevo-ticket.que-pasa.panel` | card | verificar | Panel «¿Qué pasa después de enviar?»: los pasos del pipeline, la cola por cuota y dónde seguir el avance. |  |
| `nuevo-ticket.que-pasa.paso` | card | verificar | Un paso del pipeline, en orden (triage, diseño, Gate 1, build, QA, Gate 2, seguridad, Gate 3, producción); los gates llevan «Firma humana». |  |

## nueva-funcionalidad

Alta de funcionalidad: objetivo, criterios Given/When/Then, impacto en vivo y documentos. Ruta `/nueva-funcionalidad` · roles: owner, admin, architect, approver, member.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `nueva-funcionalidad.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `nueva-funcionalidad.formulario.nombre` | input | escribir | Nombre de la funcionalidad (5 a 200 caracteres). |  |
| `nueva-funcionalidad.formulario.objetivo` | input | escribir | Objetivo de negocio (20 caracteres o más); con 10+ caracteres entre nombre y objetivo consulta el impacto. |  |
| `nueva-funcionalidad.criterios.fila` 🔁 | table | verificar | Criterio de aceptación. |  |
| `nueva-funcionalidad.criterios.dado` 🔁 | input | escribir | Campo «Dado que…» del criterio. |  |
| `nueva-funcionalidad.criterios.cuando` 🔁 | input | escribir | Campo «Cuando…» del criterio. |  |
| `nueva-funcionalidad.criterios.entonces` 🔁 | input | escribir | Campo «Entonces…» del criterio. |  |
| `nueva-funcionalidad.criterios.quitar` 🔁 | button | click | Quita el criterio. |  |
| `nueva-funcionalidad.criterios.error` | card | verificar | Errores de un criterio (Dado, Cuando y Entonces: 5 a 500 caracteres cada uno), bajo su fila; se repite por fila. Un criterio en blanco no se envía. | el criterio está a medias o el backend lo rechaza |
| `nueva-funcionalidad.criterios.agregar` | button | click | Agrega un criterio vacío. |  |
| `nueva-funcionalidad.formulario.cancelar` | button | click | Vuelve a Tickets sin crear la funcionalidad. |  |
| `nueva-funcionalidad.formulario.enviar` | button | click | Crea la funcionalidad con los criterios completos y los adjuntos subidos; abre su detalle. | deshabilitado mientras se envía o hay adjuntos subiendo |
| `nueva-funcionalidad.errores.resumen` | card | verificar | Aviso «Revisa los campos marcados» (role=alert); lista los errores de campos que el formulario no muestra. | al enviar con errores, o tras un 422 VALIDACION_ENTRADA |
| `nueva-funcionalidad.errores.nombre` | card | verificar | Error del campo nombre (5 a 200 caracteres), del portal o del 422 del backend; el control queda con aria-invalid. | el campo no cumple su regla al enviar, o el backend lo rechaza |
| `nueva-funcionalidad.errores.objetivo` | card | verificar | Error del campo objetivo (20 a 10.000 caracteres), del portal o del 422 del backend; el control queda con aria-invalid. | el campo no cumple su regla al enviar, o el backend lo rechaza |
| `nueva-funcionalidad.impacto.vacio` | card | verificar | Sin impacto estimado («—»). |  |
| `nueva-funcionalidad.impacto.modulo` 🔁 | card | verificar | Módulo afectado según el impacto estimado. | si hay impacto |
| `nueva-funcionalidad.impacto.tabla` 🔁 | card | verificar | Tabla de base de datos afectada según el impacto estimado. | si hay impacto |
| `nueva-funcionalidad.impacto.confianza` | card | verificar | Confianza del impacto estimado (%). | si hay impacto |
| `nueva-funcionalidad.adjuntos.zona` | card | soltar | Zona de arrastre de archivos. Pegar una captura en cualquier parte del formulario también la adjunta. |  |
| `nueva-funcionalidad.adjuntos.elegir` | button | click | Abre el selector de archivos del sistema. |  |
| `nueva-funcionalidad.adjuntos.entrada` | input | subir | input type=file oculto: usar setInputFiles. Acepta imágenes, video, PDF, DOCX, MD, TXT, CSV y ZIP. |  |
| `nueva-funcionalidad.adjuntos.archivo` 🔁 | table | verificar | Archivo agregado: nombre, tamaño y estado. |  |
| `nueva-funcionalidad.adjuntos.progreso` 🔁 | card | verificar | Barra de progreso de la subida (aria-label «Subida de <nombre>»). | mientras el archivo se sube |
| `nueva-funcionalidad.adjuntos.error` 🔁 | card | verificar | Motivo del rechazo o del fallo: tipo no permitido, vacío, más de 25 MB, más de 10 archivos o cuota de almacenamiento agotada (con trace_id si viene del backend). | archivo rechazado o con error |
| `nueva-funcionalidad.adjuntos.reintentar` 🔁 | button | click | Vuelve a subir el archivo. | solo en fallos de red o del almacenamiento |
| `nueva-funcionalidad.adjuntos.quitar` 🔁 | button | click | Quita el archivo de la lista y cancela su subida si está en curso. |  |
| `nueva-funcionalidad.vista-previa.panel` | card | verificar | Vista previa de lo que recibe el arquitecto IA: nombre, objetivo y criterios Dado/cuando/entonces; se actualiza al escribir. |  |
| `nueva-funcionalidad.vista-previa.vacio` | card | verificar | Vista previa sin contenido todavía. | sin nombre, objetivo ni criterios |
| `nueva-funcionalidad.vista-previa.nombre` | card | verificar | Nombre de la funcionalidad en la vista previa («(falta)» si está vacío). |  |
| `nueva-funcionalidad.vista-previa.objetivo` | card | verificar | Objetivo de negocio en la vista previa («(falta)» si está vacío). |  |
| `nueva-funcionalidad.vista-previa.criterio` | card | verificar | Un criterio como lo envía el formulario: «Dado …, cuando …, entonces …»; se repite por criterio no vacío y marca «(falta)» en la parte vacía. | hay criterios con algo escrito |
| `nueva-funcionalidad.que-pasa.panel` | card | verificar | Panel «¿Qué pasa después de enviar?»: los pasos del pipeline, la cola por cuota y dónde seguir el avance. |  |
| `nueva-funcionalidad.que-pasa.paso` | card | verificar | Un paso del pipeline, en orden (triage, diseño, Gate 1, build, QA, Gate 2, seguridad, Gate 3, producción); los gates llevan «Firma humana». |  |

## aprobaciones

Gates esperando la decisión del rol. Ruta `/aprobaciones` · roles: owner, admin, architect, approver.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `aprobaciones.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `aprobaciones.lista.tarjeta` 🔁 | card | verificar | Gate pendiente: número, título, qué se aprueba y desde cuándo espera. |  |
| `aprobaciones.lista.revisar` 🔁 | button | click | Abre el detalle del ticket para decidir. |  |
| `aprobaciones.lista.vacio` | card | verificar | «Sin resultados». | si no hay gates pendientes |

## auditoria

Explorador de eventos de auditoría con búsqueda, filtro por actor y paginación. Ruta `/auditoria` · roles: owner, admin, architect.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `auditoria.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `auditoria.filtros.busqueda` | input | escribir | Búsqueda por texto: consulta al dejar de escribir (350 ms) y vuelve a la página 1. |  |
| `auditoria.filtros.actor` | input | seleccionar | Filtro por tipo de actor: humano, agente IA o sistema; vuelve a la página 1. |  |
| `auditoria.lista.tabla` | table | verificar | Tabla de eventos: fecha, ticket, actor, evento, modelo y sello. |  |
| `auditoria.lista.fila` 🔁 | table | click | Evento; si tiene ticket, abre su expediente. |  |
| `auditoria.cadena.resumen` | card | verificar | Cantidad de eventos registrados hoy. |  |
| `auditoria.paginacion.contenedor` | card | verificar | Paginación del listado. | solo si hay más de una página |
| `auditoria.paginacion.anterior` | button | click | Va a la página anterior. | deshabilitado en la página 1 |
| `auditoria.paginacion.estado` | card | verificar | Texto «Página N de M». |  |
| `auditoria.paginacion.siguiente` | button | click | Va a la página siguiente; la página queda en la URL (?page=N). | deshabilitado en la última página |

## expediente

Expediente de auditoría de un ticket. Ruta `/auditoria/:numero` · roles: owner, admin, architect, approver.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `expediente.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `expediente.cabecera.volver` | link | click | Vuelve a Auditoría. |  |
| `expediente.solicitante.datos` | card | verificar | Quién pidió el ticket, desde dónde y cuándo. |  |
| `expediente.integridad.estado` | card | verificar | Integridad de la cadena: «✓ íntegra» o «✗ rota». |  |
| `expediente.firmas.tabla` | table | verificar | Firmas de gates con actor, fecha, comentario y sello. |  |
| `expediente.pasos.lista` | card | verificar | Pasos LLM por etapa: modelo, tier, validación, tokens y duración. |  |
| `expediente.cambios.lista` | card | verificar | Archivos cambiados con su racional y enlace al PR. |  |
| `expediente.cambios.vacio` | card | verificar | Sin cambios registrados. | si el ticket no tiene cambios |
| `expediente.eventos.lista` | card | verificar | Eventos de auditoría del ticket. | si el backend los incluye |

## onboarding

Asistente de alta de 5 pasos: repositorio, herramientas, mapa de ramas, documentación y activación, con progreso y reanudación (?paso=1…5). Ruta `/onboarding` · roles: owner, admin.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `onboarding.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `onboarding.progreso.barra` | card | verificar | Barra de progreso (role=progressbar): pasos completos de 5, deducidos de lo que el backend tiene configurado. |  |
| `onboarding.pasos.paso` | button | click | Un paso del asistente; se repite en orden (repositorio, herramientas, ramas, documentación, activación). El actual lleva aria-current=step; abre ese paso (?paso=N). |  |
| `onboarding.panel.contenido` | card | verificar | Panel del paso actual; al cambiar de paso, el foco va a su título. |  |
| `onboarding.panel.estado` | card | verificar | Si el paso actual está completo o pendiente. |  |
| `onboarding.panel.configurar` | link | click | Lleva a Conexiones para configurar el paso (hasta que lleguen los formularios de cada paso). | paso documentación |
| `onboarding.navegacion.anterior` | button | click | Paso anterior; deshabilitado en el primero. |  |
| `onboarding.navegacion.siguiente` | button | click | Paso siguiente. | no está en el último paso |
| `onboarding.resumen.conexion` | card | verificar | Una conexión configurada (proveedor, nombre, estado y última prueba), con sus acciones; se repite por conexión. | pasos repositorio y herramientas |
| `onboarding.resumen.vacio` | card | verificar | Aún no hay conexiones de esa categoría o el mapa de ramas no está configurado. | pasos repositorio, herramientas y ramas |
| `onboarding.resumen.ramas` | card | verificar | Mapa de ramas guardado (feature → staging → producción); se actualiza al guardar. | paso ramas, con el mapa configurado |
| `onboarding.resumen.documentos` | card | verificar | Cantidad de documentos subidos para el grafo. | paso documentación |
| `onboarding.resumen.grafo` | card | verificar | Avance del grafo de código en porcentaje. | paso activación, con el estado del grafo disponible |
| `onboarding.resumen.fabrica` | card | verificar | Si la fábrica está activa o no. | paso activación |
| `onboarding.categorias.repositorio` | card | verificar | Sección de conexiones de la categoría repositorio: su lista y el botón para agregar. | paso repositorio |
| `onboarding.categorias.tickets` | card | verificar | Sección de conexiones de la categoría tickets: su lista y el botón para agregar. | paso herramientas |
| `onboarding.categorias.cicd` | card | verificar | Sección de conexiones de la categoría cicd: su lista y el botón para agregar. | paso herramientas |
| `onboarding.categorias.database` | card | verificar | Sección de conexiones de la categoría database: su lista y el botón para agregar. | paso herramientas |
| `onboarding.formulario.abrir` 🔁 | button | click | Abre el formulario de alta de la categoría; uno por sección. | pasos repositorio y herramientas, owner y admin |
| `onboarding.formulario.proveedor` | input | seleccionar | Proveedor de la conexión (select); al cambiarlo se vacían los campos. Los que el backend aún no sabe probar aparecen deshabilitados como «(próximamente)»; viene elegido el primero disponible. | después de onboarding.formulario.abrir |
| `onboarding.formulario.nombre` | input | escribir | Nombre de la conexión, opcional. | después de onboarding.formulario.abrir |
| `onboarding.formulario.repo-url` | input | escribir | URL del repositorio (https://). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: repositorios, GitHub Issues, GitHub Actions y GitLab CI |
| `onboarding.formulario.token` | input | escribir | Token de acceso (password). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: repositorios, GitHub Issues, GitHub Actions y GitLab CI |
| `onboarding.formulario.url` | input | escribir | URL del servicio (https://). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: Jira, Kallicode Help, CoreStream y Jenkins |
| `onboarding.formulario.proyecto` | input | escribir | Clave del proyecto. Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: Jira |
| `onboarding.formulario.usuario` | input | escribir | Usuario. Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: Jira, Jenkins y bases de datos |
| `onboarding.formulario.api-token` | input | escribir | Token de la API (password). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: Jira, Kallicode Help, CoreStream y Jenkins |
| `onboarding.formulario.webhook-secret` | input | escribir | Secreto del webhook, opcional (password). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: gestores de tickets y CI/CD |
| `onboarding.formulario.host` | input | escribir | Servidor de la base de datos. Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: bases de datos |
| `onboarding.formulario.puerto` | input | escribir | Puerto (1–65535). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: bases de datos |
| `onboarding.formulario.base` | input | escribir | Nombre de la base de datos. Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: bases de datos |
| `onboarding.formulario.contrasena` | input | escribir | Contraseña (password). Con error lleva aria-invalid y aria-describedby al mensaje. | proveedor: bases de datos |
| `onboarding.formulario.error` 🔁 | card | verificar | Mensaje de error de un campo (requerido, URL https o puerto); se repite por campo con error. | al enviar con campos inválidos |
| `onboarding.formulario.cancelar` | button | click | Cierra el formulario (alta o rotación) sin guardar nada. | después de onboarding.formulario.abrir o onboarding.conexiones.rotar |
| `onboarding.formulario.enviar` | button | click | Prueba y guarda la conexión (alta) o las credenciales nuevas (rotación): el backend la prueba antes (CREDENCIALES_INVALIDAS o PERMISOS_EXCESIVOS si falla, y no cambia nada). | después de onboarding.formulario.abrir o onboarding.conexiones.rotar |
| `onboarding.formulario.exito` | card | verificar | Conexión creada con los permisos verificados, o credenciales rotadas (role=status). | después de crear la conexión |
| `onboarding.formulario.webhook` | input | verificar | URL del webhook entrante para configurar en la herramienta (solo lectura); no se vuelve a mostrar. | después de crear una conexión de tickets o CI/CD |
| `onboarding.conexiones.probar` 🔁 | button | click | Prueba la conexión (aria-label con el proveedor). | pasos repositorio y herramientas, owner y admin |
| `onboarding.conexiones.resultado` 🔁 | card | verificar | Resultado de la prueba (role=status): responde, o falló con el motivo del backend. | después de onboarding.conexiones.probar |
| `onboarding.conexiones.renombrar` 🔁 | button | click | Abre la edición del nombre de la conexión (aria-label con el proveedor). | pasos repositorio y herramientas, owner y admin |
| `onboarding.conexiones.nombre` | input | escribir | Nombre nuevo de la conexión. | después de onboarding.conexiones.renombrar |
| `onboarding.conexiones.guardar` | button | click | Guarda el nombre (PATCH); deshabilitado si está vacío. | después de onboarding.conexiones.renombrar |
| `onboarding.conexiones.eliminar` 🔁 | button | click | Pide confirmación para eliminar la conexión (aria-label con el proveedor). | pasos repositorio y herramientas, solo owner |
| `onboarding.eliminacion.dialogo` | modal | verificar | Confirmación de eliminación (role=dialog): avisa que se borran las credenciales; Escape lo cierra salvo mientras se elimina. | después de onboarding.conexiones.eliminar |
| `onboarding.eliminacion.cancelar` | button | click | Cierra la confirmación sin eliminar. | después de onboarding.conexiones.eliminar |
| `onboarding.eliminacion.confirmar` | button | click | Elimina la conexión (DELETE); CONEXION_EN_USO si un repositorio tiene trabajos activos. | después de onboarding.conexiones.eliminar |
| `onboarding.ramas.feature` | input | escribir | Patrón de las ramas de trabajo (admite *, p. ej. feature/*). Sin mapa trae el valor sugerido; con error lleva aria-invalid y aria-describedby a la ayuda y al mensaje. | paso ramas |
| `onboarding.ramas.staging` | input | escribir | Rama de staging. Sin mapa trae el valor sugerido; con error lleva aria-invalid y aria-describedby a la ayuda y al mensaje. | paso ramas |
| `onboarding.ramas.produccion` | input | escribir | Rama de producción; distinta de la de staging. Sin mapa trae el valor sugerido; con error lleva aria-invalid y aria-describedby a la ayuda y al mensaje. | paso ramas |
| `onboarding.ramas.error` 🔁 | card | verificar | Mensaje de error de un campo (obligatorio, nombre de rama inválido, * fuera del patrón o producción igual a staging); se repite por campo con error. | al guardar con campos inválidos o con un 422 del backend |
| `onboarding.ramas.guardar` | button | click | Guarda el mapa (PUT /connections/branch-map); JOBS_ACTIVOS si hay trabajos en Build o Deploy. | paso ramas |
| `onboarding.ramas.exito` | card | verificar | Región role=status: queda vacía y muestra «Mapa de ramas guardado.» tras guardar; se vacía al volver a editar. | paso ramas |
| `onboarding.grafo.barra` | card | verificar | Avance del grafo (role=progressbar, aria-valuenow de 0 a 100); bajo el 100 % se vuelve a consultar cada 15 s. | paso activación, con el estado del grafo disponible |
| `onboarding.grafo.detalle` | card | verificar | Fase, definiciones y documentos del grafo, y la fecha de actualización si la hay. | paso activación, con el estado del grafo disponible |
| `onboarding.activacion.pendientes` | card | verificar | Pasos obligatorios que faltan para encender la fábrica (repositorio y mapa de ramas); describe al botón de encender. | paso activación, owner, fábrica apagada y con pasos obligatorios sin hacer |
| `onboarding.activacion.ir` 🔁 | button | click | Lleva al paso que falta, con el foco en su título; se repite por paso pendiente. | dentro de onboarding.activacion.pendientes |
| `onboarding.activacion.encender` | button | click | Abre la confirmación para encender la fábrica. Con pasos pendientes lleva aria-disabled=true y no abre nada. | paso activación, owner y fábrica apagada |
| `onboarding.activacion.solo-owner` | card | verificar | Aviso de que solo el owner puede encender la fábrica. | paso activación, admin y fábrica apagada |
| `onboarding.activacion.resultado` | card | verificar | Región role=status: vacía y oculta; tras encender dice cuántas líneas de producción se activaron. | paso activación |
| `onboarding.activacion.tablero` | link | click | Lleva al tablero; recibe el foco al encender la fábrica. | paso activación, fábrica activa |
| `onboarding.encendido.dialogo` | modal | verificar | Confirmación para encender la fábrica (role=dialog): avisa que se crean las líneas y queda en auditoría; Escape lo cierra salvo mientras se enciende. | después de onboarding.activacion.encender |
| `onboarding.encendido.cancelar` | button | click | Cierra la confirmación sin encender. | después de onboarding.activacion.encender |
| `onboarding.encendido.confirmar` | button | click | Enciende la fábrica (POST /onboarding/complete); un error (p. ej. ONBOARDING_INCOMPLETO) queda en el diálogo. | después de onboarding.activacion.encender |
| `onboarding.formulario.mostrar` 🔁 | button | click | Muestra u oculta lo escrito en un campo secreto (aria-pressed, aria-controls al campo); se repite por secreto. Al guardar se vuelve a ocultar. | después de onboarding.formulario.abrir o onboarding.conexiones.rotar |
| `onboarding.conexiones.rotar` 🔁 | button | click | Abre el formulario de bóveda para rotar las credenciales (aria-label con el proveedor); al cerrarlo el foco vuelve aquí. | pasos repositorio y herramientas, owner y admin |

## conexiones

Conexiones por categoría, prueba de conexión y encendido de la fábrica. Ruta `/conexiones` · roles: owner, admin.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `conexiones.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `conexiones.cabecera.encender` | button | click | Enciende la fábrica y muestra cuántas líneas se activaron en conexiones.avisos.mensaje. | solo owner y si la fábrica no está activa |
| `conexiones.avisos.mensaje` | card | verificar | Resultado de la última prueba de conexión o del encendido. | después de probar o encender |
| `conexiones.categorias.repositorio` | card | verificar | Tarjeta de la categoría «repositorio». |  |
| `conexiones.categorias.tickets` | card | verificar | Tarjeta de la categoría «tickets». |  |
| `conexiones.categorias.cicd` | card | verificar | Tarjeta de la categoría «cicd». |  |
| `conexiones.categorias.database` | card | verificar | Tarjeta de la categoría «database». |  |
| `conexiones.lista.vacio` 🔁 | card | verificar | «Sin resultados» dentro de una categoría. | categoría sin conexiones |
| `conexiones.lista.fila` 🔁 | table | verificar | Conexión: proveedor, nombre y fecha del último test. |  |
| `conexiones.lista.estado` 🔁 | card | verificar | Estado de la conexión (conectada u otro). |  |
| `conexiones.lista.probar` 🔁 | button | click | Prueba la conexión y muestra el resultado en conexiones.avisos.mensaje. | owner o admin |
| `conexiones.ramas.feature` | input | verificar | Rama de features (solo lectura). | si la organización tiene mapa de ramas |
| `conexiones.ramas.staging` | input | verificar | Rama de staging (solo lectura). | si la organización tiene mapa de ramas |
| `conexiones.ramas.produccion` | input | verificar | Rama de producción (solo lectura). | si la organización tiene mapa de ramas |

## modelos-ia

Tiers de modelos y asignación por paso del pipeline. Ruta `/modelos` · roles: owner, admin, architect.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `modelos-ia.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `modelos-ia.tiers.tarjeta` 🔁 | card | verificar | Tier de modelo (flash, pro, fable) con su modelo y perfil. |  |
| `modelos-ia.politica.texto` | card | verificar | Política de escalado entre tiers. |  |
| `modelos-ia.asignaciones.tabla` | table | verificar | Asignación de modelo por paso del pipeline. |  |
| `modelos-ia.asignaciones.fila` 🔁 | table | verificar | Paso con su etapa, tier, modelo y si es escalable. |  |

## consumo

Cuota de LOC del ciclo, consumo por ticket y solicitud de upgrade. Es la única vista disponible con la suscripción suspendida. Ruta `/consumo` · roles: owner, admin, architect, member.

| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |
| --- | --- | --- | --- | --- |
| `consumo.vista.raiz` | card | verificar | Contenedor de la vista: indica que la vista cargó (también si muestra un error). |  |
| `consumo.cabecera.plan` | card | verificar | Plan actual. |  |
| `consumo.cabecera.upgrade` | button | click | Solicita el upgrade del plan y muestra la respuesta en consumo.avisos.mensaje. | owner o admin (también con la suscripción suspendida) |
| `consumo.avisos.mensaje` | card | verificar | Respuesta de la solicitud de upgrade. | después de solicitar el upgrade |
| `consumo.kpis.consumidas` | card | verificar | KPI: LOC consumidas en el ciclo. |  |
| `consumo.kpis.restantes` | card | verificar | KPI: LOC restantes («∞» si el plan no tiene límite). |  |
| `consumo.kpis.umbral` | card | verificar | KPI: umbral de la cuota (normal, aviso al 80 % o agotado). |  |
| `consumo.kpis.tickets` | card | verificar | KPI: tickets cerrados + en proceso. |  |
| `consumo.cuota.barra` | card | verificar | Barra de cuota: el ancho es el % consumido y el color depende del umbral. |  |
| `consumo.tickets.tabla` | table | verificar | Consumo por ticket del ciclo. |  |
| `consumo.tickets.fila` 🔁 | table | click | Ticket con sus LOC y tokens; abre su detalle. |  |
| `consumo.paginacion.contenedor` | card | verificar | Paginación del listado. | solo si hay más de una página |
| `consumo.paginacion.anterior` | button | click | Va a la página anterior. | deshabilitado en la página 1 |
| `consumo.paginacion.estado` | card | verificar | Texto «Página N de M». |  |
| `consumo.paginacion.siguiente` | button | click | Va a la página siguiente; la página queda en la URL (?page=N). | deshabilitado en la última página |
