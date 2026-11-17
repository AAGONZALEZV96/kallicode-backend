/**
 * Errores por campo de los formularios (KC-PT-06-5, cierra PT-F18).
 *
 * Los que detecta el portal antes de enviar y los que devuelve el backend en un 422
 * (VALIDACION_ENTRADA con detalle.campos = [{campo, error}]) se muestran igual: junto al
 * campo, con aria-invalid y aria-describedby, en el idioma del usuario. Las reglas del
 * portal son las de los modelos del backend (min_length y max_length), así que el 422
 * queda para lo que el portal no puede saber.
 */
import { z } from "zod";

import { ApiError } from "./client";

type Traductor = (clave: string, valores?: Record<string, string>) => string;

/** Clave del campo en el formulario → mensaje. */
export type ErroresCampo = Partial<Record<string, string>>;

const CamposEsquema = z.object({
  campos: z.array(z.object({ campo: z.string(), error: z.string() })),
});

/** Mensajes de Pydantic v2 (vienen en inglés) que el portal sabe decir en el idioma del usuario. */
const CONOCIDOS: [RegExp, (n: string, t: Traductor) => string][] = [
  [/^Field required/i, (_, t) => t("campo.requerido")],
  [/at least (\d+) characters?/i, (n, t) => t("campo.min", { n })],
  [/at most (\d+) characters?/i, (n, t) => t("campo.max", { n })],
  [/at most (\d+) items?/i, (n, t) => t("campo.max_elementos", { n })],
  [/should match pattern/i, (_, t) => t("campo.formato")],
];

/** Mensaje de un error de campo del backend; si no se reconoce, tal como llegó. */
export function mensajeDeCampo(t: Traductor, mensaje: string): string {
  for (const [patron, traducir] of CONOCIDOS) {
    const m = patron.exec(mensaje);
    if (m) return traducir(m[1] ?? "", t);
  }
  return mensaje;
}

export interface ErroresDelServidor {
  /** Errores de los campos que el formulario muestra. */
  porCampo: ErroresCampo;
  /** Los de campos que el formulario no tiene (p. ej. `adjuntos`), para el resumen. */
  sueltos: string[];
}

/**
 * Reparte los errores de un 422 entre los campos del formulario. `aCampo` traduce el
 * nombre del backend (`criterios.0.given`) a la clave del formulario, o devuelve null si
 * el formulario no lo tiene. Un 422 sin `campos` se asigna a `campoSinDetalle`, si se da
 * (p. ej. tickets.actualizar sin motivo de cancelación).
 */
export function erroresDelServidor(
  t: Traductor,
  error: unknown,
  aCampo: (campo: string) => string | null,
  campoSinDetalle?: string,
): ErroresDelServidor {
  const resultado: ErroresDelServidor = { porCampo: {}, sueltos: [] };
  if (!(error instanceof ApiError) || error.http !== 422) return resultado;

  const campos = CamposEsquema.safeParse(error.detalle);
  if (!campos.success) {
    if (campoSinDetalle) resultado.porCampo[campoSinDetalle] = error.message;
    return resultado;
  }
  for (const { campo, error: mensaje } of campos.data.campos) {
    const texto = mensajeDeCampo(t, mensaje);
    const clave = aCampo(campo);
    if (clave === null) resultado.sueltos.push(`${campo}: ${texto}`);
    else resultado.porCampo[clave] ??= texto;
  }
  return resultado;
}

/** Regla de un campo de texto, igual que su Field(min_length, max_length) del backend. */
export function errorDeTexto(
  t: Traductor,
  valor: string,
  { min = 0, max }: { min?: number; max?: number },
): string | undefined {
  const largo = valor.trim().length;
  if (min > 0 && largo === 0) return t("campo.requerido");
  if (largo < min) return t("campo.min", { n: String(min) });
  if (max !== undefined && valor.length > max) return t("campo.max", { n: String(max) });
  return undefined;
}

/** El primer campo con error, en el orden en que aparecen en el formulario. */
export const primeroConError = (orden: readonly string[], errores: ErroresCampo) =>
  orden.find((campo) => errores[campo]);

/** Id del control de un campo: `${idBase}-criterios-0-given` para `criterios.0.given`. */
export const idDeCampo = (idBase: string, campo: string) =>
  `${idBase}-${campo.replace(/\./g, "-")}`;

/** Atributos de accesibilidad de un control con (o sin) error. */
export function atributosDeError(idError: string, error: string | undefined) {
  return error
    ? { "aria-invalid": true as const, "aria-describedby": idError }
    : { "aria-invalid": undefined, "aria-describedby": undefined };
}

/** Un 422 de validación: lo que el usuario corrige editando los campos. */
export const esErrorDeValidacion = (error: unknown) =>
  error instanceof ApiError && error.http === 422;
