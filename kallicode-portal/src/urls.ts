/**
 * Saneado de URLs que llegan del backend y terminan en un href (KC-PT-03-4, cierra PT-F22).
 *
 * pr_url y similares vienen de datos externos (el repositorio del cliente): un
 * `javascript:` o `data:` en un href ejecuta código al hacer clic. React solo avisa
 * de los `javascript:`, no los bloquea.
 */

/**
 * Devuelve la URL normalizada si es http(s) absoluta y sin credenciales; si no, null.
 * Las credenciales se rechazan porque permiten disfrazar el destino
 * (`https://github.com@malo.example` lleva a malo.example).
 */
export function sanearUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    if (u.username || u.password) return null;
    return u.href;
  } catch {
    return null;
  }
}
