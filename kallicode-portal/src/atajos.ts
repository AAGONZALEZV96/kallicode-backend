/**
 * Atajos de teclado globales (KC-PT-11-6). Solo actúan cuando no se está escribiendo en un
 * campo, sin Ctrl/Alt/Cmd y sin un diálogo abierto. Las letras son las mismas en los tres
 * idiomas; la ayuda (?) las muestra con su descripción traducida.
 *
 * WCAG 2.1.4 (nivel A): los atajos de una sola tecla se pueden desactivar (con un lector de
 * pantalla o con dictado podrían dispararse sin querer). La preferencia queda en este navegador.
 */

export type AccionAtajo = { tipo: "ayuda" } | { tipo: "buscar" } | { tipo: "ir"; ruta: string };

export interface Atajo {
  /** Teclas en orden: ["g", "t"] es «g y luego t». */
  teclas: string[];
  accion: AccionAtajo;
  /** Clave i18n de la descripción (las de «ir» se muestran como «Ir a <destino>»). */
  descripcion: string;
}

export const ATAJOS: Atajo[] = [
  { teclas: ["?"], accion: { tipo: "ayuda" }, descripcion: "atajos.ayuda" },
  { teclas: ["/"], accion: { tipo: "buscar" }, descripcion: "atajos.buscar" },
  { teclas: ["n"], accion: { tipo: "ir", ruta: "/nuevo-ticket" }, descripcion: "nav.newticket" },
  { teclas: ["g", "i"], accion: { tipo: "ir", ruta: "/" }, descripcion: "nav.dashboard" },
  { teclas: ["g", "t"], accion: { tipo: "ir", ruta: "/tickets" }, descripcion: "nav.tickets" },
  {
    teclas: ["g", "a"],
    accion: { tipo: "ir", ruta: "/aprobaciones" },
    descripcion: "nav.approvals",
  },
  { teclas: ["g", "u"], accion: { tipo: "ir", ruta: "/auditoria" }, descripcion: "nav.audit" },
  { teclas: ["g", "c"], accion: { tipo: "ir", ruta: "/consumo" }, descripcion: "nav.usage" },
  { teclas: ["g", "e"], accion: { tipo: "ir", ruta: "/equipo" }, descripcion: "nav.equipo" },
  { teclas: ["g", "p"], accion: { tipo: "ir", ruta: "/perfil" }, descripcion: "nav.perfil" },
];

/** Tiempo para la segunda tecla de una secuencia («g» y luego «t»). */
export const ESPERA_SECUENCIA_MS = 1500;

/** ¿El foco está en algo donde se escribe? Ahí las teclas son texto, no atajos. */
export function esEditable(objetivo: EventTarget | null): boolean {
  if (!(objetivo instanceof HTMLElement)) return false;
  return (
    objetivo.isContentEditable ||
    objetivo.closest('[contenteditable=""], [contenteditable="true"]') !== null ||
    objetivo instanceof HTMLTextAreaElement ||
    objetivo instanceof HTMLSelectElement ||
    (objetivo instanceof HTMLInputElement &&
      !["button", "checkbox", "radio", "submit", "reset", "file", "range", "color"].includes(
        objetivo.type,
      ))
  );
}

const PREFERENCIA = "kc_atajos";

/** Activos salvo que se hayan desactivado en este navegador. */
export function leerAtajosActivos(): boolean {
  try {
    return localStorage.getItem(PREFERENCIA) !== "desactivados";
  } catch {
    return true;
  }
}

export function guardarAtajosActivos(activos: boolean) {
  try {
    if (activos) localStorage.removeItem(PREFERENCIA);
    else localStorage.setItem(PREFERENCIA, "desactivados");
  } catch {
    // Sin localStorage la preferencia dura hasta recargar.
  }
}

/**
 * La acción que corresponde a la tecla, dada la tecla anterior pendiente de una secuencia.
 * Devuelve también si la tecla deja una secuencia abierta («g»).
 */
export function resolverTecla(
  tecla: string,
  pendiente: string | null,
): { accion: AccionAtajo | null; abre: string | null } {
  if (pendiente) {
    const atajo = ATAJOS.find(
      (a) => a.teclas.length === 2 && a.teclas[0] === pendiente && a.teclas[1] === tecla,
    );
    return { accion: atajo?.accion ?? null, abre: null };
  }
  if (ATAJOS.some((a) => a.teclas.length === 2 && a.teclas[0] === tecla))
    return { accion: null, abre: tecla };
  const atajo = ATAJOS.find((a) => a.teclas.length === 1 && a.teclas[0] === tecla);
  return { accion: atajo?.accion ?? null, abre: null };
}
