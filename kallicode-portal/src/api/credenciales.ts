/**
 * Custodia de las credenciales del portal (KC-PT-02-2, cierra PT-F02).
 *
 * Los tokens viven solo en memoria: en sessionStorage/localStorage cualquier script
 * inyectado (XSS) podría leerlos. Consecuencia buscada: recargar la página o abrir otra
 * pestaña pide iniciar sesión de nuevo (KC-PT-02-5 preserva la ruta de destino).
 *
 * El cliente solo conoce la interfaz, así que la custodia puede cambiar (p. ej. el
 * refresh en una cookie httpOnly) sin tocar client.ts.
 */

export interface Credenciales {
  access: string;
  refresh: string;
}

export interface AlmacenCredenciales {
  leer(): Credenciales | null;
  guardar(credenciales: Credenciales): void;
  borrar(): void;
}

export function crearAlmacenEnMemoria(): AlmacenCredenciales {
  let actuales: Credenciales | null = null;
  return {
    leer: () => actuales,
    guardar: (credenciales) => {
      actuales = { ...credenciales };
    },
    borrar: () => {
      actuales = null;
    },
  };
}
