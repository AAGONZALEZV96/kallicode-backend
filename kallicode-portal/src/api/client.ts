/**
 * Cliente de la API Kallicode.
 *
 * - Adjunta el Bearer token y un X-Trace-Id por petición.
 * - Ante 401 intenta UNA renovación con el refresh token (rotativo) y
 *   reintenta la petición; si falla, cierra la sesión.
 * - Convierte el sobre de error estándar del backend
 *   {error:{codigo,mensaje,detalle,trace_id}} en ApiError tipado, con el
 *   mensaje listo para mostrarse en pantalla.
 */

export interface SobreError {
  codigo: string;
  mensaje: string;
  detalle?: Record<string, unknown>;
  trace_id?: string;
}

export class ApiError extends Error {
  codigo: string;
  http: number;
  detalle?: Record<string, unknown>;
  traceId?: string;
  constructor(http: number, e: SobreError) {
    super(e.mensaje);
    this.codigo = e.codigo;
    this.http = http;
    this.detalle = e.detalle;
    this.traceId = e.trace_id;
  }
}

const BASE = import.meta.env.VITE_API_URL || "";

let accessToken: string | null = sessionStorage.getItem("kc_access");
let refreshToken: string | null = sessionStorage.getItem("kc_refresh");
let alExpirar: (() => void) | null = null;

export function fijarSesion(access: string, refresh: string): void {
  accessToken = access;
  refreshToken = refresh;
  sessionStorage.setItem("kc_access", access);
  sessionStorage.setItem("kc_refresh", refresh);
}

export function limpiarSesion(): void {
  accessToken = refreshToken = null;
  sessionStorage.removeItem("kc_access");
  sessionStorage.removeItem("kc_refresh");
}

export function haySesion(): boolean {
  return !!accessToken;
}

export function alExpirarSesion(fn: () => void): void {
  alExpirar = fn;
}

async function renovar(): Promise<boolean> {
  if (!refreshToken) return false;
  const r = await fetch(`${BASE}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!r.ok) return false;
  const j = await r.json();
  fijarSesion(j.access_token, j.refresh_token);
  return true;
}

async function pedir<T>(
  metodo: string,
  ruta: string,
  cuerpo?: unknown,
  reintento = false,
): Promise<T> {
  const cabeceras: Record<string, string> = {
    "X-Trace-Id": `trw_${crypto.randomUUID().slice(0, 20)}`,
  };
  if (cuerpo !== undefined) cabeceras["Content-Type"] = "application/json";
  if (accessToken) cabeceras["Authorization"] = `Bearer ${accessToken}`;

  const r = await fetch(`${BASE}${ruta}`, {
    method: metodo,
    headers: cabeceras,
    body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
  });

  if (r.status === 401 && !reintento && !ruta.startsWith("/api/v1/auth/")) {
    if (await renovar()) return pedir<T>(metodo, ruta, cuerpo, true);
    limpiarSesion();
    alExpirar?.();
  }
  if (!r.ok) {
    let sobre: SobreError = { codigo: "ERROR_DESCONOCIDO", mensaje: `Error ${r.status}` };
    try {
      const j = await r.json();
      if (j.error) sobre = j.error;
    } catch {
      /* cuerpo no JSON (p. ej. 401 de webhook) */
    }
    throw new ApiError(r.status, sobre);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const api = {
  get: <T>(ruta: string) => pedir<T>("GET", ruta),
  post: <T>(ruta: string, cuerpo?: unknown) => pedir<T>("POST", ruta, cuerpo),
  patch: <T>(ruta: string, cuerpo?: unknown) => pedir<T>("PATCH", ruta, cuerpo),
  put: <T>(ruta: string, cuerpo?: unknown) => pedir<T>("PUT", ruta, cuerpo),
  del: <T>(ruta: string) => pedir<T>("DELETE", ruta),
};
