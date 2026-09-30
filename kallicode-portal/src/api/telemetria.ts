/**
 * Telemetría de errores de cliente (KC-PT-01-3).
 *
 * Punto único por el que pasa todo error capturado por un <LimiteError>.
 * Hoy solo emite una línea estructurada en consola: la API v1.1 no expone aún
 * un endpoint para errores de cliente. El envío agrupado al backend llega con
 * KC-PT-11-5; cuando exista, se implementa aquí sin tocar los componentes.
 */
import { ApiError } from "./client";

export interface ErrorCliente {
  /** Identificador corto que la pantalla de recuperación muestra al usuario. */
  id: string;
  nivel: "raiz" | "ruta";
  ruta: string;
  mensaje: string;
  /** trace_id del backend si el error vino de la API. */
  traceId?: string;
  pilaComponentes?: string;
}

export function nuevoIdError(): string {
  return `err_${crypto.randomUUID().slice(0, 8)}`;
}

export function describirError(
  error: unknown,
  datos: Omit<ErrorCliente, "mensaje" | "traceId">,
): ErrorCliente {
  return {
    ...datos,
    mensaje: error instanceof Error ? error.message : String(error),
    traceId: error instanceof ApiError ? error.traceId : undefined,
  };
}

export function reportarErrorCliente(evento: ErrorCliente): void {
  console.error("kc.error_cliente", evento);
}
