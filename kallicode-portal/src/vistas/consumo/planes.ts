/** Orden y nombre de los planes (KC-PT-09-1), compartidos por Consumo y el comparador. */

/** Orden de los planes (plans._ORDEN): solo se puede pedir uno superior al vigente. */
const ORDEN_PLANES = ["starter", "growth", "enterprise"];

/** Posición del plan de menor a mayor; uno que el portal no conoce va al final. */
export function nivelPlan(codigo: string): number {
  const i = ORDEN_PLANES.indexOf(codigo);
  return i === -1 ? Infinity : i;
}

/** El plan con el nombre del catálogo («growth» → «Growth») sin esperar a GET /plans. */
export const nombrePlan = (codigo: string) => codigo.charAt(0).toUpperCase() + codigo.slice(1);
