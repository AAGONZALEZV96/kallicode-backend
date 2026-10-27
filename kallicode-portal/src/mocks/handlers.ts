/**
 * Dobles MSW de /api/v1 que consume el portal (KC-PT-01-2).
 *
 * - Rutas con prefijo `*` para aceptar cualquier origen (VITE_API_URL en pruebas).
 * - Las rutas protegidas exigen `Authorization: Bearer`, como el backend real:
 *   sin token responden 401 con el sobre de error estándar.
 * - Cada prueba puede sobrescribir un endpoint con `server.use(...)`.
 */
import {
  http,
  HttpResponse,
  type DefaultBodyType,
  type HttpResponseResolver,
  type JsonBodyType,
} from "msw";

import * as d from "./datos";

const API = "*/api/v1";
/** Origen de las URLs firmadas del doble (el blob real es otro servicio). */
export const BLOB = "http://blob.test";

/** Sobre de error del backend: {error:{codigo,mensaje,detalle,trace_id,timestamp}}. */
export function sobreError(http: number, codigo: string, mensaje: string) {
  return HttpResponse.json(
    {
      error: {
        codigo,
        mensaje,
        trace_id: "tr_msw_prueba",
        timestamp: "2026-09-25T12:00:00Z",
      },
    },
    { status: http },
  );
}

/** Envuelve un resolver para exigir Bearer, como `usuario_actual` del backend. */
function conSesion<P extends Record<string, string>>(
  resolver: HttpResponseResolver<P, DefaultBodyType>,
): HttpResponseResolver<P, DefaultBodyType> {
  return (info) => {
    if (!info.request.headers.get("Authorization")?.startsWith("Bearer ")) {
      return sobreError(401, "NO_AUTENTICADO", "Falta el token de acceso.");
    }
    return resolver(info);
  };
}

function ticketONoEncontrado(numero: string, datos: JsonBodyType) {
  return d.tickets.some((t) => t.id === numero)
    ? HttpResponse.json(datos)
    : sobreError(404, "NO_ENCONTRADO", "El recurso no existe.");
}

export const handlers = [
  // ---- sesión ----
  http.post(`${API}/auth/login`, async ({ request }) => {
    const { email, password } = (await request.json()) as { email: string; password: string };
    if (email !== d.usuarioOwner.email || password !== d.PASSWORD_DEMO) {
      return sobreError(401, "AUTH_CREDENCIALES_INVALIDAS", "Correo o contraseña incorrectos.");
    }
    return HttpResponse.json(d.sesion);
  }),
  http.post(`${API}/auth/refresh`, async ({ request }) => {
    const { refresh_token } = (await request.json()) as { refresh_token: string };
    if (!refresh_token) return sobreError(401, "AUTH_REFRESH_INVALIDO", "Refresh inválido.");
    return HttpResponse.json({
      access_token: "acc_2",
      refresh_token: "ref_2",
      acceso_restringido: false,
    });
  }),
  http.post(
    `${API}/auth/logout`,
    conSesion(() => HttpResponse.json({ cerradas: 1 })),
  ),
  http.get(
    `${API}/users/me`,
    conSesion(() => HttpResponse.json(d.perfilOwner)),
  ),

  // ---- organización y tablero ----
  http.get(
    `${API}/organization`,
    conSesion(() => HttpResponse.json(d.organizacion)),
  ),
  http.get(
    `${API}/dashboard/summary`,
    conSesion(() => HttpResponse.json(d.resumenTablero)),
  ),
  http.get(
    `${API}/dashboard/lines`,
    conSesion(() => HttpResponse.json(d.lineas)),
  ),
  http.get(
    `${API}/dashboard/kanban`,
    conSesion(() => HttpResponse.json(d.kanban)),
  ),

  // ---- tickets ----
  http.get(
    `${API}/tickets`,
    conSesion(() => HttpResponse.json(d.paginar(d.tickets))),
  ),
  http.post(
    `${API}/tickets`,
    conSesion(() =>
      HttpResponse.json(
        { id: "KC-1004", etapa: "triage", duplicado_potencial: null },
        { status: 201 },
      ),
    ),
  ),
  // Adjuntos: URL firmada y PUT directo al blob (fuera de la API).
  http.post(
    `${API}/tickets/attachments`,
    conSesion(async ({ request }) => {
      const cuerpo = (await request.json().catch(() => ({}))) as { nombre_archivo?: string };
      // Id predecible en las pruebas: sale del nombre del archivo.
      const nombre = cuerpo.nombre_archivo ?? "archivo";
      const adjunto_id = `att_${nombre}`;
      return HttpResponse.json(
        {
          adjunto_id,
          upload_url: `${BLOB}/evidencia/${adjunto_id}/${encodeURIComponent(nombre)}?sig=msw`,
          expira_en: "2026-09-25T12:15:00Z",
        },
        { status: 201 },
      );
    }),
  ),
  http.put(`${BLOB}/*`, () => new HttpResponse(null, { status: 201 })),
  http.post(
    `${API}/tickets/dedup-preview`,
    conSesion(() =>
      HttpResponse.json({
        similares: [
          {
            ticket_id: "KC-1001",
            titulo: d.tickets[0].titulo,
            similitud: 0.82,
            etapa: "triage",
            resuelto_en: null,
          },
        ],
      }),
    ),
  ),
  http.post(
    `${API}/tickets/impact-preview`,
    conSesion(() =>
      HttpResponse.json({ modulos: ["billing"], tablas: ["core.facturas"], confianza: 0.8 }),
    ),
  ),
  http.post(
    `${API}/tickets/features`,
    conSesion(() =>
      HttpResponse.json(
        {
          id: "KC-1005",
          etapa: "triage",
          impacto_estimado: { modulos: [], tablas: [] },
        },
        { status: 201 },
      ),
    ),
  ),
  http.get(
    `${API}/tickets/:numero`,
    conSesion<{ numero: string }>(({ params }) => {
      const detalle = d.detalleTicket(params.numero);
      return detalle
        ? HttpResponse.json(detalle)
        : sobreError(404, "NO_ENCONTRADO", "El recurso no existe.");
    }),
  ),
  http.patch(
    `${API}/tickets/:numero`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, { ok: true })),
  ),

  // ---- pestañas del detalle (404 = "aún sin datos") ----
  http.get(
    `${API}/tickets/:numero/spec`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, d.spec)),
  ),
  http.get(
    `${API}/tickets/:numero/evidence`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, d.evidencia)),
  ),
  http.get(
    `${API}/tickets/:numero/security`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, d.hallazgos)),
  ),
  http.get(
    `${API}/tickets/:numero/usage`,
    conSesion<{ numero: string }>(({ params }) =>
      ticketONoEncontrado(params.numero, d.consumoTicket),
    ),
  ),
  http.get(
    `${API}/tickets/:numero/activity`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, d.actividad)),
  ),
  http.post(
    `${API}/tickets/:numero/gates/:gate/:accion`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, { ok: true })),
  ),

  // ---- aprobaciones, notificaciones y auditoría ----
  http.get(
    `${API}/approvals/pending`,
    conSesion(() => HttpResponse.json(d.aprobaciones)),
  ),
  http.get(
    `${API}/notifications`,
    conSesion(() => HttpResponse.json(d.notificaciones)),
  ),
  http.get(
    `${API}/audit/events`,
    conSesion(() => HttpResponse.json(d.eventosAuditoria)),
  ),
  http.get(
    `${API}/audit/tickets/:numero`,
    conSesion<{ numero: string }>(({ params }) => ticketONoEncontrado(params.numero, d.expediente)),
  ),

  // ---- conexiones y onboarding ----
  http.get(
    `${API}/connections`,
    conSesion(() => HttpResponse.json(d.conexiones)),
  ),
  http.post(
    `${API}/connections/:id/test`,
    conSesion(() => HttpResponse.json({ estado: "conectada", detalle: { ok: true } })),
  ),
  http.post(
    `${API}/onboarding/complete`,
    conSesion(() => HttpResponse.json({ activado: true, lineas_activadas: 3, pendientes: [] })),
  ),

  // ---- modelos IA, consumo y plan ----
  http.get(
    `${API}/models/providers`,
    conSesion(() => HttpResponse.json(d.proveedoresLLM)),
  ),
  http.get(
    `${API}/models/assignments`,
    conSesion(() => HttpResponse.json(d.asignacionesLLM)),
  ),
  http.get(
    `${API}/usage/summary`,
    conSesion(() => HttpResponse.json(d.resumenConsumo)),
  ),
  http.get(
    `${API}/usage/by-ticket`,
    conSesion(() => HttpResponse.json(d.consumoPorTicket)),
  ),
  http.post(
    `${API}/plans/upgrade-request`,
    conSesion(() =>
      HttpResponse.json(
        {
          solicitud_id: "upg_1",
          estado: "registrada",
          mensaje: "El equipo comercial te contactará hoy mismo.",
        },
        { status: 202 },
      ),
    ),
  ),
];
