/**
 * Mapa de ramas del asistente (KC-PT-07-3): valores sugeridos y validación antes del
 * PUT /connections/branch-map. El backend (BranchMapIn) solo limita el largo a 100 y exige
 * staging distinta de producción; el portal además rechaza lo que git no acepta como
 * nombre de rama (reglas de `git check-ref-format`), para no guardar un mapa inservible.
 */

export const CAMPOS_RAMA = ["feature", "staging", "produccion"] as const;
export type CampoRama = (typeof CAMPOS_RAMA)[number];
export type MapaRamas = Record<CampoRama, string>;

export const esCampoRama = (campo: string): campo is CampoRama =>
  (CAMPOS_RAMA as readonly string[]).includes(campo);

/** Los DEFAULT de core.branch_map: lo que se propone si aún no hay mapa. */
export const MAPA_SUGERIDO: MapaRamas = {
  feature: "feature/*",
  staging: "develop",
  produccion: "main",
};

/** Largo máximo de cada campo (BranchMapIn: max_length=100). */
export const LARGO_MAXIMO = 100;

export type ErrorRama = "requerido" | "max" | "formato" | "comodin" | "iguales";

/** Error de un campo, o undefined si está bien. `mapa` trae los tres valores escritos. */
export function errorDeRama(campo: CampoRama, mapa: MapaRamas): ErrorRama | undefined {
  const v = mapa[campo].trim();
  if (!v) return "requerido";
  if (v.length > LARGO_MAXIMO) return "max";
  if (v.includes("*") && campo !== "feature") return "comodin";
  if (!esNombreDeRama(v.replace(/\*/g, "x"))) return "formato";
  if (campo === "produccion" && v === mapa.staging.trim()) return "iguales";
  return undefined;
}

/** Nombre de rama válido para git (sin el comodín, que se reemplaza antes). */
function esNombreDeRama(v: string): boolean {
  return !(
    v === "@" ||
    // Caracteres de control, espacios y los prohibidos por git.
    [...v].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127) ||
    /[\s~^:?[\\]/.test(v) ||
    /\.\.|@\{|\/\//.test(v) ||
    /^\/|\/$|\.$|\.lock$/.test(v) ||
    // Ningún tramo puede empezar con punto ni terminar en .lock.
    /(^|\/)\.|\.lock\//.test(v)
  );
}

/** Lo que se envía: cada campo sin espacios alrededor. */
export const limpiar = (mapa: MapaRamas): MapaRamas => ({
  feature: mapa.feature.trim(),
  staging: mapa.staging.trim(),
  produccion: mapa.produccion.trim(),
});
