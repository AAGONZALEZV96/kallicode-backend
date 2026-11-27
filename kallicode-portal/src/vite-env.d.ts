/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origen de la API (vacío: mismo origen, con el proxy de Vite en desarrollo). */
  readonly VITE_API_URL?: string;
}
