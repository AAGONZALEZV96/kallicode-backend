/**
 * Tipado sin aserciones (KC-PT-11-3, PT-F19): en vez de afirmar un tipo con `as`, se comprueba
 * en tiempo de ejecución y TypeScript estrecha el tipo a partir de esa comprobación.
 */

/**
 * ¿`valor` es una de las `opciones`? Estrecha un string que llega de la URL, de un <select> o
 * del backend al literal correspondiente («owner», «es», «loc_netas»…).
 */
export function esUnoDe<T extends string>(
  opciones: readonly T[],
  valor: string | null | undefined,
): valor is T {
  return opciones.some((opcion) => opcion === valor);
}

/** Un registro con una entrada por cada clave, calculada con `valor`. */
export function registroDe<K extends string, V>(
  claves: readonly K[],
  valor: (clave: K) => V,
): Record<K, V> {
  const registro: Partial<Record<K, V>> = {};
  for (const clave of claves) registro[clave] = valor(clave);
  // Siempre se cumple (se acaba de llenar cada clave): la comprobación es lo que le permite a
  // TypeScript pasar de Partial a Record sin una aserción.
  if (!tieneTodas(registro, claves)) throw new Error("registroDe: falta una clave");
  return registro;
}

function tieneTodas<K extends string, V>(
  registro: Partial<Record<K, V>>,
  claves: readonly K[],
): registro is Record<K, V> {
  return claves.every((clave) => clave in registro);
}
