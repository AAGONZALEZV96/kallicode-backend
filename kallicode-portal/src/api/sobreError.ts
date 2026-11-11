/**
 * Lectura del cuerpo de una respuesta de error (KC-PT-06-5).
 *
 * El backend responde con su sobre {error:{codigo, mensaje, detalle, trace_id}}; en un 422
 * de validación, detalle.campos = [{campo, error}] (main._validacion). También se acepta
 * un Problem Details de RFC 7807/9457 (title, detail, status y errores por campo en
 * `errors` o `invalid-params`), así un servicio que responda en ese formato se muestra
 * igual: los dos terminan en la misma forma de SobreError.
 */
import { z } from "zod";

import type { SobreError } from "./client";

const SobreEsquema = z.object({
  error: z.object({
    codigo: z.string(),
    mensaje: z.string().optional(),
    detalle: z.record(z.string(), z.unknown()).nullish(),
    trace_id: z.string().nullish(),
  }),
});

/** Un error por campo en Problem Details: cada servicio nombra distinto sus partes. */
const ErrorProblemaEsquema = z.object({
  campo: z.string().optional(),
  name: z.string().optional(),
  pointer: z.string().optional(),
  error: z.string().optional(),
  reason: z.string().optional(),
  detail: z.string().optional(),
});

const ProblemaEsquema = z.object({
  title: z.string(),
  status: z.number().optional(),
  detail: z.string().optional(),
  codigo: z.string().optional(),
  code: z.string().optional(),
  trace_id: z.string().optional(),
  errors: z.array(ErrorProblemaEsquema).optional(),
  "invalid-params": z.array(ErrorProblemaEsquema).optional(),
});

/** JSON Pointer (/criterios/0/given) → ruta con puntos (criterios.0.given), como el backend. */
const rutaDePuntero = (puntero: string) =>
  puntero.replace(/^#?\//, "").split("/").filter(Boolean).join(".");

export function leerSobreError(cuerpo: unknown, http: number): SobreError {
  const sobre = SobreEsquema.safeParse(cuerpo);
  if (sobre.success) {
    const { codigo, mensaje, detalle, trace_id } = sobre.data.error;
    return {
      codigo,
      mensaje: mensaje ?? `Error ${http}`,
      ...(detalle ? { detalle } : {}),
      ...(trace_id ? { trace_id } : {}),
    };
  }

  const problema = ProblemaEsquema.safeParse(cuerpo);
  if (problema.success) {
    const p = problema.data;
    const errores = p.errors ?? p["invalid-params"] ?? [];
    const campos = errores.flatMap((e) => {
      const campo = e.campo ?? e.name ?? (e.pointer ? rutaDePuntero(e.pointer) : undefined);
      const error = e.error ?? e.reason ?? e.detail;
      return campo && error ? [{ campo, error }] : [];
    });
    return {
      codigo: p.codigo ?? p.code ?? (http === 422 ? "VALIDACION_ENTRADA" : `HTTP_${http}`),
      mensaje: p.detail ?? p.title,
      ...(campos.length > 0 ? { detalle: { campos } } : {}),
      ...(p.trace_id ? { trace_id: p.trace_id } : {}),
    };
  }

  return { codigo: "ERROR_DESCONOCIDO", mensaje: `Error ${http}` };
}
