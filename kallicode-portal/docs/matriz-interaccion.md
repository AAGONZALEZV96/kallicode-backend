# Matriz de interacción del portal (selectores v1)

> Generado desde `contratos/selectores-portal-v1.json` por `src/selectores.test.ts`. No se edita a mano: se cambia el JSON y se corre `npm run selectores:doc`.

Contrato de selectores `data-testid` del portal con el equipo de Testing (KC-PT-05-5): **224 selectores en 16 vistas**.

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
| [layout](#layout) | `*` | owner, admin, architect, approver, member, viewer | 18 |
| [bandeja-notificaciones](#bandeja-notificaciones) | `*` | owner, admin, architect, approver, member, viewer | 6 |
| [comun](#comun) | `*` | owner, admin, architect, approver, member, viewer | 2 |
| [error](#error) | `*` | owner, admin, architect, approver, member, viewer | 6 |
| [login](#login) | `/login` | sin sesión | 4 |
| [tablero](#tablero) | `/` | owner, admin, architect, approver, member, viewer | 17 |
| [tickets](#tickets) | `/tickets` | owner, admin, architect, approver, member, viewer | 13 |
| [detalle-ticket](#detalle-ticket) | `/tickets/:numero` | owner, admin, architect, approver, member, viewer | 57 |
| [nuevo-ticket](#nuevo-ticket) | `/nuevo-ticket` | owner, admin, architect, approver, member | 21 |
| [nueva-funcionalidad](#nueva-funcionalidad) | `/nueva-funcionalidad` | owner, admin, architect, approver, member | 23 |
| [aprobaciones](#aprobaciones) | `/aprobaciones` | owner, admin, architect, approver | 4 |
| [auditoria](#auditoria) | `/auditoria` | owner, admin, architect | 10 |
| [expediente](#expediente) | `/auditoria/:numero` | owner, admin, architect, approver | 9 |
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
| `detalle-ticket.resumen.pr` | link | click | Abre el PR del ticket en una pestaña nueva. | si el ticket tiene PR con URL http(s) |
| `detalle-ticket.visualizadores.lista` | card | verificar | Visualizadores del ticket (iniciales). |  |
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
| `detalle-ticket.acciones.cancelar` | button | click | Pide un motivo (prompt del navegador, mínimo 5 caracteres), cancela el ticket y vuelve a Tickets. | creador del ticket, owner o admin; no en deploy, producción ni cancelado |

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
| `nueva-funcionalidad.criterios.agregar` | button | click | Agrega un criterio vacío. |  |
| `nueva-funcionalidad.formulario.cancelar` | button | click | Vuelve a Tickets sin crear la funcionalidad. |  |
| `nueva-funcionalidad.formulario.enviar` | button | click | Crea la funcionalidad con los criterios completos y los adjuntos subidos; abre su detalle. | deshabilitado mientras se envía o hay adjuntos subiendo |
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
