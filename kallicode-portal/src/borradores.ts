/**
 * Borradores de formularios (KC-PT-11-6): lo que se escribe en «Nuevo ticket» o «Nueva
 * funcionalidad» se guarda en este navegador mientras se escribe, para no perderlo al recargar,
 * al salir de la vista o si la sesión expira a mitad de camino.
 *
 * - Uno por organización, persona y formulario (localStorage).
 * - Se borra al enviar, al descartarlo, al cerrar sesión (computadores compartidos) y a los
 *   7 días. Los archivos adjuntos no se guardan: se suben al agregarlos.
 * - Lo leído se valida con un esquema Zod: un borrador viejo o editado a mano se ignora.
 */
import { z, type ZodType } from "zod";

const PREFIJO = "kc_borrador:";
export const VIGENCIA_BORRADOR_MS = 7 * 24 * 60 * 60 * 1000;

/** Dueño del borrador: la persona en su organización. */
export interface Dueno {
  id: string;
  org_id: string;
}

const clave = (dueno: Dueno, formulario: string) =>
  `${PREFIJO}${dueno.org_id}:${dueno.id}:${formulario}`;

export function leerBorrador<T>(
  dueno: Dueno,
  formulario: string,
  esquema: ZodType<T>,
  ahora = Date.now(),
): { datos: T; guardado: Date } | null {
  try {
    const texto = localStorage.getItem(clave(dueno, formulario));
    if (!texto) return null;
    const leido = z.object({ guardado: z.string(), datos: esquema }).safeParse(JSON.parse(texto));
    if (!leido.success) return null;
    const guardado = new Date(leido.data.guardado);
    if (Number.isNaN(guardado.getTime()) || ahora - guardado.getTime() > VIGENCIA_BORRADOR_MS)
      return null;
    return { datos: leido.data.datos, guardado };
  } catch {
    return null; // Sin localStorage o con un JSON roto: como si no hubiera borrador.
  }
}

export function guardarBorrador(dueno: Dueno, formulario: string, datos: unknown) {
  try {
    localStorage.setItem(
      clave(dueno, formulario),
      JSON.stringify({ guardado: new Date().toISOString(), datos }),
    );
  } catch {
    // Sin espacio o sin localStorage: el formulario sigue funcionando, sin borrador.
  }
}

export function borrarBorrador(dueno: Dueno, formulario: string) {
  try {
    localStorage.removeItem(clave(dueno, formulario));
  } catch {
    // Nada que borrar.
  }
}

/** Al cerrar sesión: no quedan borradores de nadie en este navegador. */
export function borrarTodosLosBorradores() {
  try {
    for (const k of Object.keys(localStorage))
      if (k.startsWith(PREFIJO)) localStorage.removeItem(k);
  } catch {
    // Sin localStorage: no hay borradores.
  }
}
