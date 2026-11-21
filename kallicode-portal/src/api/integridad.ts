/** Lectura de una verificación de sellos del tramo de un ticket (KC-PT-08-4). */
import type { Integridad } from "./tipos";

export type EstadoSello = "confirmado" | "alterado" | "sin-confirmar";

/**
 * Qué se sabe del sello de un evento según la última verificación del tramo. El backend
 * informa solo el primer evento alterado (el menor id): los anteriores quedan confirmados y
 * de los posteriores no se sabe nada.
 */
export function estadoSello(eventoId: number, verificacion: Integridad): EstadoSello {
  const roto = verificacion.primer_evento_roto;
  if (verificacion.integra || roto === null || eventoId < roto) return "confirmado";
  return eventoId === roto ? "alterado" : "sin-confirmar";
}

/** Sello abreviado (8…4), como en el PDF del expediente; el completo va en un title. */
export function abreviarSello(sello: string): string {
  return sello.length > 14 ? `${sello.slice(0, 8)}…${sello.slice(-4)}` : sello;
}
