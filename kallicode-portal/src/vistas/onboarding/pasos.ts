/**
 * Pasos del asistente de onboarding (KC-PT-07-1) y en cuál retomar.
 *
 * El avance no se guarda aparte: se deduce de lo que el backend ya tiene configurado,
 * así el asistente retoma donde quedó la organización en cualquier navegador o equipo.
 * Los pasos obligatorios son el repositorio, el mapa de ramas y la activación; las
 * herramientas (tickets, CI/CD, base de datos) y la documentación son opcionales.
 */
import type { Conexiones, EstadoGrafo } from "../../api/tipos";

export const PASOS = [
  "repositorio",
  "herramientas",
  "ramas",
  "documentacion",
  "activacion",
] as const;
export type Paso = (typeof PASOS)[number];

export const OPCIONALES: ReadonlySet<Paso> = new Set<Paso>(["herramientas", "documentacion"]);

/** Categorías de conexión del paso «herramientas» (las del repositorio van en el primero). */
export const CATEGORIAS_HERRAMIENTAS = ["tickets", "cicd", "database"] as const;

export interface DatosOnboarding {
  conexiones: Conexiones;
  grafo: EstadoGrafo | undefined;
  fabricaActiva: boolean;
}

const conectada = (datos: DatosOnboarding, categorias: readonly string[]) =>
  datos.conexiones.conexiones.some(
    (c) => categorias.includes(c.categoria) && c.estado === "conectada",
  );

/** Si el paso ya quedó hecho con lo que el backend tiene configurado. */
export function pasoCompleto(paso: Paso, datos: DatosOnboarding): boolean {
  switch (paso) {
    case "repositorio":
      return conectada(datos, ["repositorio"]);
    case "herramientas":
      return conectada(datos, CATEGORIAS_HERRAMIENTAS);
    case "ramas":
      return datos.conexiones.mapa_ramas !== null;
    case "documentacion":
      return (datos.grafo?.metricas.documentos ?? 0) > 0;
    case "activacion":
      return datos.fabricaActiva;
  }
}

export interface EstadoAsistente {
  completos: Record<Paso, boolean>;
  cantidadCompletos: number;
  /** Índice (desde 0) del paso donde retomar si no se pidió otro. */
  reanudar: number;
}

/**
 * Retoma en el primer paso obligatorio sin hacer, para que un opcional sin completar no
 * detenga a nadie. Con todo hecho (fábrica activa), en la activación.
 */
export function estadoAsistente(datos: DatosOnboarding): EstadoAsistente {
  const completos = Object.fromEntries(PASOS.map((p) => [p, pasoCompleto(p, datos)])) as Record<
    Paso,
    boolean
  >;
  const pendiente = PASOS.findIndex((p) => !OPCIONALES.has(p) && !completos[p]);
  return {
    completos,
    cantidadCompletos: PASOS.filter((p) => completos[p]).length,
    reanudar: pendiente === -1 ? PASOS.length - 1 : pendiente,
  };
}

/** Pasos obligatorios previos a la activación que aún no están hechos (KC-PT-07-5). */
export const pasosFaltantes = (datos: DatosOnboarding): Paso[] =>
  PASOS.filter((p) => p !== "activacion" && !OPCIONALES.has(p) && !pasoCompleto(p, datos));

/** Paso pedido en la URL (?paso=1…5) o null si no es válido. */
export function pasoDeUrl(valor: string | null): number | null {
  if (!valor || !/^[1-9]\d*$/.test(valor)) return null;
  const n = Number(valor);
  return n >= 1 && n <= PASOS.length ? n - 1 : null;
}
