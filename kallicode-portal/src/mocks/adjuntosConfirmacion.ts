import { http, HttpResponse } from "msw";

/**
 * Respuestas deterministas para el flujo de confirmación de adjuntos.
 * Estas pruebas comprueban la interfaz; ClamAV real se prueba en Backend.
 */
export const handlersAdjuntosConfirmacion = [
  http.post(
    "*/api/v1/tickets/attachments/:adjuntoId/confirm",
    ({ params }) =>
      HttpResponse.json(
        {
          adjunto_id: String(params.adjuntoId),
          estado: "pendiente_escaneo",
          escaneo: "pendiente",
          tamano_bytes: 2048,
          content_type: "application/octet-stream",
          motivo: null,
          cuota: {
            usados: 2048,
            limite: 104857600,
            porcentaje: 0,
            umbral: "normal",
          },
        },
        { status: 202 },
      ),
  ),

  http.get(
    "*/api/v1/tickets/attachments/:adjuntoId/status",
    ({ params }) =>
      HttpResponse.json({
        adjunto_id: String(params.adjuntoId),
        estado: "listo",
        escaneo: "limpio",
        tamano_bytes: 2048,
        content_type: "application/octet-stream",
        motivo: null,
      }),
  ),
];