/**
 * Enlaces firmados de la evidencia QA (KC-PT-05-2).
 *
 * Cada GET /tickets/{n}/evidence trae firmas nuevas (SAS de solo lectura, 10 min en
 * blob.url_descarga) y la pestaña se refresca cada REFRESCO_TICKET_MS. Si se usaran tal
 * cual, las miniaturas se descargarían de nuevo en cada refresco y un video en
 * reproducción se reiniciaría. Por eso, mientras el enlace anterior siga vigente se
 * conserva, y se cambia por el nuevo cuando le queda menos de MARGEN_ENLACE_MS.
 */
import { replaceEqualDeep } from "@tanstack/react-query";

import type { Evidencia } from "./tipos";

export const MARGEN_ENLACE_MS = 60_000;

export type ArchivoEvidencia = Evidencia["matriz"][number]["evidencias"][number];

/** Identifica la misma evidencia entre respuestas: su ruta en el blob o su adjunto. */
const claveDe = (archivo: ArchivoEvidencia) => archivo.blob_ref ?? archivo.adjunto_id ?? null;

const finDe = (expira: string | null | undefined) => (expira ? Date.parse(expira) : NaN);

/** Ya venció. Sin fecha válida no se sabe, así que se deja usar. */
export function vencido(expira: string | null | undefined, ahora = Date.now()): boolean {
  return finDe(expira) <= ahora;
}

/** Le queda más que el margen. Sin fecha válida no se reutiliza. */
function vigente(expira: string | null | undefined, ahora: number): boolean {
  return finDe(expira) - ahora > MARGEN_ENLACE_MS;
}

const esEvidencia = (datos: unknown): datos is Evidencia =>
  typeof datos === "object" && datos !== null && Array.isArray((datos as Evidencia).matriz);

/**
 * `structuralSharing` de la pestaña Evidencia: arma la respuesta nueva con los enlaces
 * vigentes de la anterior. Si una evidencia ya no trae enlace, tampoco se le conserva
 * (pudo bloquearse). El resto pasa por replaceEqualDeep, como el comportamiento por
 * defecto de TanStack, así que lo que no cambió mantiene su identidad y no se vuelve a pintar.
 */
export function conservarEnlaces(previos: unknown, nuevos: unknown, ahora = Date.now()): unknown {
  if (!esEvidencia(previos) || !esEvidencia(nuevos)) return replaceEqualDeep(previos, nuevos);

  const vigentes = new Map<string, ArchivoEvidencia>();
  for (const fila of previos.matriz) {
    for (const archivo of fila.evidencias) {
      const clave = claveDe(archivo);
      if (clave && archivo.download_url && vigente(archivo.expira_en, ahora)) {
        vigentes.set(clave, archivo);
      }
    }
  }

  const conEnlacesPrevios: Evidencia = {
    ...nuevos,
    matriz: nuevos.matriz.map((fila) => ({
      ...fila,
      evidencias: fila.evidencias.map((archivo) => {
        const clave = claveDe(archivo);
        const anterior = clave ? vigentes.get(clave) : undefined;
        return anterior && archivo.download_url
          ? { ...archivo, download_url: anterior.download_url, expira_en: anterior.expira_en }
          : archivo;
      }),
    })),
  };
  return replaceEqualDeep(previos, conEnlacesPrevios);
}
