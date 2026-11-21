/**
 * Lectura de las trazas de un paso LLM desde su URL firmada de Blob (KC-PT-08-2).
 *
 * - La URL ya lleva su autorización (SAS): se pide sin credenciales ni el token del portal,
 *   que nunca debe salir hacia el almacenamiento, y sin Referer.
 * - El navegador solo puede leerla si el almacenamiento permite CORS al dominio del portal; si
 *   no, o si la URL venció, falla y el panel ofrece abrirla en otra pestaña.
 * - El contenido se muestra como texto (React lo escapa): nunca se interpreta como HTML.
 */
import { sanearUrl } from "../urls";

export type TipoTraza = "entrada" | "salida";

/** Más que esto no se muestra en el panel: el resto se ve abriendo la traza aparte. */
export const MAX_CARACTERES_TRAZA = 200_000;

export interface Traza {
  /** JSON con sangría, o el texto tal cual si no es JSON. */
  texto: string;
  esJson: boolean;
  /** Se cortó en MAX_CARACTERES_TRAZA. */
  truncada: boolean;
}

export class ErrorTraza extends Error {
  constructor(public readonly estado: number | null) {
    super(estado === null ? "traza ilegible desde el portal" : `traza respondió ${estado}`);
    this.name = "ErrorTraza";
  }
}

export async function leerTraza(url: string, signal?: AbortSignal): Promise<Traza> {
  const segura = sanearUrl(url);
  if (!segura) throw new ErrorTraza(null);
  let r: Response;
  try {
    r = await fetch(segura, { signal, credentials: "omit", referrerPolicy: "no-referrer" });
  } catch (e) {
    if (signal?.aborted) throw e;
    // CORS o red: el navegador no deja leerla desde el portal.
    throw new ErrorTraza(null);
  }
  if (!r.ok) throw new ErrorTraza(r.status);
  return formatearTraza(await r.text());
}

export function formatearTraza(crudo: string): Traza {
  let texto = crudo;
  let esJson = false;
  try {
    texto = JSON.stringify(JSON.parse(crudo), null, 2);
    esJson = true;
  } catch {
    // No es JSON: se muestra tal cual.
  }
  const truncada = texto.length > MAX_CARACTERES_TRAZA;
  return { texto: truncada ? texto.slice(0, MAX_CARACTERES_TRAZA) : texto, esJson, truncada };
}
