/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origen de la API (vacío: mismo origen, con el proxy de Vite en desarrollo). */
  readonly VITE_API_URL?: string;
  /** Adónde se envían las Web Vitals (KC-PT-11-2); sin valor no se envían. */
  readonly VITE_TELEMETRIA_URL?: string;
}
