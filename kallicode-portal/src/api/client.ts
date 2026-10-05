/**
 * Cliente de la API Kallicode.
 *
 * - Adjunta el Bearer token y un X-Trace-Id por petición.
 * - Ante 401 intenta UNA renovación con el refresh token (rotativo) y
 *   reintenta la petición; si falla, cierra la sesión.
 * - La renovación es single-flight (KC-PT-02-1, cierra PT-F01): el refresh es de
 *   un solo uso y el backend trata su reutilización como replay y cierra todas las
 *   sesiones, así que varias peticiones con 401 comparten una única renovación.
 * - Renueva de forma proactiva 60 s antes del `exp` del access token.
 * - Los tokens se guardan solo en memoria, vía AlmacenCredenciales (KC-PT-02-2).
 * - cerrarSesion() revoca la sesión en el servidor con POST /auth/logout (KC-PT-02-3).
 * - Convierte el sobre de error estándar del backend
 *   {error:{codigo,mensaje,detalle,trace_id}} en ApiError tipado, con el
 *   mensaje listo para mostrarse en pantalla.
 */
import { crearAlmacenEnMemoria, type Credenciales } from "./credenciales";

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

/** Antelación con la que se renueva el access token antes de su `exp`. */
export const MARGEN_RENOVACION_MS = 60_000;

const almacen = crearAlmacenEnMemoria();
let alExpirar: (() => void) | null = null;
let renovacionEnCurso: Promise<boolean> | null = null;
let temporizadorRenovacion: ReturnType<typeof setTimeout> | undefined;

/** `exp` del JWT en milisegundos, o null si el token no es un JWT legible. */
function expiracionMs(token: string): number | null {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const { exp } = JSON.parse(atob(payload)) as { exp?: unknown };
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

/**
 * Programa la renovación proactiva. Usa el reloj del navegador: si está desfasado
 * respecto del servidor, el 401 sigue cubierto por la renovación reactiva.
 */
function programarRenovacion(): void {
  clearTimeout(temporizadorRenovacion);
  const access = almacen.leer()?.access;
  const exp = access ? expiracionMs(access) : null;
  if (exp === null) return;
  const espera = Math.max(exp - Date.now() - MARGEN_RENOVACION_MS, 0);
  // Un fallo aquí no cierra la sesión: la próxima petición con 401 lo resuelve.
  temporizadorRenovacion = setTimeout(() => void renovar().catch(() => {}), espera);
}

export function fijarSesion(access: string, refresh: string): void {
  almacen.guardar({ access, refresh });
  programarRenovacion();
}

export function limpiarSesion(): void {
  almacen.borrar();
  clearTimeout(temporizadorRenovacion);
}

export function haySesion(): boolean {
  return almacen.leer() !== null;
}

export function alExpirarSesion(fn: () => void): void {
  alExpirar = fn;
}

/**
 * Cierra la sesión y la revoca en el servidor (KC-PT-02-3, cierra PT-F03).
 *
 * La sesión local se borra antes de esperar al backend: si este tarda o no responde,
 * el usuario ya está fuera y un login nuevo no se ve afectado. Si el access venció,
 * se renueva solo para poder revocar el refresh resultante; sin eso, el refresh
 * seguiría vivo 30 días.
 */
export async function cerrarSesion(): Promise<void> {
  // Una renovación en curso deja vivo en el servidor un refresh nuevo: se revoca ese.
  await renovacionEnCurso?.catch(() => false);
  const credenciales = almacen.leer();
  limpiarSesion();
  if (!credenciales) return;
  try {
    const r = await enviarLogout(credenciales);
    if (r.status === 401) {
      const nuevas = await solicitarTokens(credenciales.refresh);
      if (nuevas) await enviarLogout(nuevas);
    }
  } catch {
    /* sin red: la sesión local ya está cerrada y el refresh caduca solo */
  }
}

function enviarLogout({ access, refresh }: Credenciales): Promise<Response> {
  return fetch(`${BASE}/api/v1/auth/logout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${access}`,
      "X-Trace-Id": nuevoTraceId(),
    },
    body: JSON.stringify({ refresh_token: refresh }),
  });
}

/** Single-flight: mientras haya una renovación en curso, todos esperan la misma. */
function renovar(): Promise<boolean> {
  renovacionEnCurso ??= pedirRenovacion().finally(() => {
    renovacionEnCurso = null;
  });
  return renovacionEnCurso;
}

async function pedirRenovacion(): Promise<boolean> {
  const usado = almacen.leer()?.refresh;
  if (!usado) return false;
  const nuevas = await solicitarTokens(usado);
  if (!nuevas) return false;
  // Si la sesión se cerró mientras tanto, no se revive con los tokens nuevos.
  if (almacen.leer()?.refresh !== usado) return false;
  fijarSesion(nuevas.access, nuevas.refresh);
  return true;
}

async function solicitarTokens(refresh: string): Promise<Credenciales | null> {
  const r = await fetch(`${BASE}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!r.ok) return null;
  const j = await r.json();
  return { access: j.access_token, refresh: j.refresh_token };
}

function nuevoTraceId(): string {
  return `trw_${crypto.randomUUID().slice(0, 20)}`;
}

async function pedir<T>(
  metodo: string,
  ruta: string,
  cuerpo?: unknown,
  reintento = false,
): Promise<T> {
  const cabeceras: Record<string, string> = {
    "X-Trace-Id": nuevoTraceId(),
  };
  if (cuerpo !== undefined) cabeceras["Content-Type"] = "application/json";
  const tokenEnviado = almacen.leer()?.access;
  if (tokenEnviado) cabeceras["Authorization"] = `Bearer ${tokenEnviado}`;

  const r = await fetch(`${BASE}${ruta}`, {
    method: metodo,
    headers: cabeceras,
    body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
  });

  if (r.status === 401 && !reintento && !ruta.startsWith("/api/v1/auth/")) {
    // Otra petición ya renovó mientras esta viajaba: basta reintentar con el token nuevo.
    const tokenActual = almacen.leer()?.access;
    if (tokenActual && tokenActual !== tokenEnviado) return pedir<T>(metodo, ruta, cuerpo, true);
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
