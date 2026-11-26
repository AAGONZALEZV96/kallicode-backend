/**
 * React.lazy con un reintento (KC-PT-11-1). Tras un despliegue, los archivos de la versión
 * anterior ya no existen y la descarga de una vista falla: se recarga la página, que trae el
 * index nuevo con los nombres de archivo nuevos. Si vuelve a fallar poco después de recargar (sin
 * conexión, servidor caído), el error llega al LimiteError de la ruta, que ofrece reintentar: así
 * nunca se recarga en bucle.
 */
import { lazy, type ComponentType } from "react";

/** Momento de la última recarga por una descarga fallida (sessionStorage). */
export const MARCA_RECARGA = "kc_recarga_por_version";

/** Una descarga que falla antes de esto desde la última recarga ya no recarga otra vez. */
export const ESPERA_ENTRE_RECARGAS_MS = 10_000;

/** La recarga pasa por aquí para poder simularla en las pruebas (jsdom no recarga). */
export const navegador = { recargar: () => window.location.reload() };

function recargoHacePoco(): boolean {
  try {
    const ultima = Number(sessionStorage.getItem(MARCA_RECARGA) ?? 0);
    return Date.now() - ultima < ESPERA_ENTRE_RECARGAS_MS;
  } catch {
    return true; // Sin sessionStorage no se puede evitar un bucle de recargas: no se recarga.
  }
}

export function cargaDiferida<T extends ComponentType>(importar: () => Promise<{ default: T }>) {
  return lazy(() =>
    importar().catch((error: unknown) => {
      if (recargoHacePoco()) throw error;
      try {
        sessionStorage.setItem(MARCA_RECARGA, String(Date.now()));
      } catch {
        throw error;
      }
      navegador.recargar();
      // La página se recarga: la vista no llega a montarse.
      return new Promise<never>(() => {});
    }),
  );
}
