/**
 * Destino tras el login a partir de `?siguiente=` (KC-PT-02-5).
 *
 * Solo rutas del propio portal: un destino hacia otro origen (https://…, //…) o una ruta
 * que al normalizarse empieza por "//" (p. ej. "/a/..//otro.sitio", que el navegador lee
 * como otro sitio) se descarta para que el login no sirva de redirección abierta.
 */
export function destinoSeguro(siguiente: string | null): string {
  if (!siguiente?.startsWith("/")) return "/";
  try {
    const url = new URL(siguiente, window.location.origin);
    const ruta = url.pathname + url.search + url.hash;
    return url.origin === window.location.origin && !ruta.startsWith("//") ? ruta : "/";
  } catch {
    return "/";
  }
}
