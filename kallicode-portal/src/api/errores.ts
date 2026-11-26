/**
 * Lo que una vista necesita saber de un error de la API, sin depender del cliente HTTP
 * (KC-PT-11-3, PT-F19): las vistas usan la capa de servicios (consultas, errores, erroresCampo)
 * y nunca api/client. Los 422 de validación se reconocen con esErrorDeValidacion (erroresCampo).
 */
import { ApiError } from "./client";

/** Código del catálogo de errores («USUARIO_YA_EXISTE»…), o null si no es un error de la API. */
export function codigoDeError(error: unknown): string | null {
  return error instanceof ApiError ? error.codigo : null;
}
