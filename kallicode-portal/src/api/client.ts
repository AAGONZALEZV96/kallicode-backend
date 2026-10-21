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
 * - Corta cada petición a los TIEMPO_LIMITE_MS y reintenta los 429 cortos según
 *   Retry-After (KC-PT-02-4).
 * - Convierte el sobre de error estándar del backend
 *   {error:{codigo,mensaje,detalle,trace_id}} en ApiError tipado, con el
 *   mensaje listo para mostrarse en pantalla.
 * - Valida cada respuesta con el esquema Zod que pasa quien llama (KC-PT-04-2).
 */
import type { ZodType } from "zod";

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
/** Tiempo máximo hasta recibir la respuesta; luego se aborta con TIEMPO_AGOTADO. */
export const TIEMPO_LIMITE_MS = 15_000;
/** Un 429 se reintenta solo si Retry-After no supera esto (si no, se muestra el error). */
export const MAX_ESPERA_429_MS = 10_000;
export const MAX_REINTENTOS_429 = 2;

const almacen = crearAlmacenEnMemoria();
let alExpirar: (() => void) | null = null;
let alRenovar: ((renovacion: Renovacion) => void) | null = null;
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
 * Lo que trae cada /auth/refresh además de los tokens: el backend recalcula ahí si la
 * suscripción está suspendida (KC-PT-03-3).
 */
export interface Renovacion {
  restringido: boolean;
}

/** Aviso tras cada renovación de tokens: el contexto de sesión revalida el perfil (KC-PT-02-5). */
export function alRenovarSesion(fn: ((renovacion: Renovacion) => void) | null): void {
  alRenovar = fn;
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
  return enviar(`${BASE}/api/v1/auth/logout`, {
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
  alRenovar?.({ restringido: nuevas.restringido });
  return true;
}

async function solicitarTokens(refresh: string): Promise<(Credenciales & Renovacion) | null> {
  const r = await enviar(`${BASE}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!r.ok) return null;
  const j = await r.json();
  return {
    access: j.access_token,
    refresh: j.refresh_token,
    restringido: j.acceso_restringido === true,
  };
}

function nuevoTraceId(): string {
  return `trw_${crypto.randomUUID().slice(0, 20)}`;
}

/**
 * fetch con tiempo límite (KC-PT-02-4, cierra PT-F14). Los fallos de transporte se
 * convierten en ApiError (http 0) para que las vistas los traten como cualquier error.
 * Se usa un temporizador propio en vez de AbortSignal.timeout para poder probarlo.
 *
 * `senal` permite que quien llama cancele (KC-PT-04-1): TanStack Query la aborta cuando
 * la consulta deja de necesitarse. Esa cancelación se propaga tal cual (AbortError):
 * no es un fallo de red y no debe mostrarse como tal.
 */
async function enviar(url: string, init: RequestInit, senal?: AbortSignal): Promise<Response> {
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), TIEMPO_LIMITE_MS);
  const cancelar = () => control.abort();
  if (senal?.aborted) cancelar();
  senal?.addEventListener("abort", cancelar, { once: true });
  try {
    return await fetch(url, { ...init, signal: control.signal });
  } catch (e) {
    if (senal?.aborted) throw e;
    throw control.signal.aborted
      ? new ApiError(0, {
          codigo: "TIEMPO_AGOTADO",
          mensaje: "El servidor tardó demasiado en responder.",
        })
      : new ApiError(0, {
          codigo: "SIN_CONEXION",
          mensaje: "No se pudo conectar con el servidor.",
        });
  } finally {
    clearTimeout(temporizador);
    senal?.removeEventListener("abort", cancelar);
  }
}

/** Milisegundos que pide esperar Retry-After (segundos o fecha HTTP), o null si no se entiende. */
function esperaRetryAfter(valor: string | null): number | null {
  if (!valor) return null;
  if (/^\d+$/.test(valor.trim())) return Number(valor) * 1000;
  const fecha = Date.parse(valor);
  return Number.isNaN(fecha) ? null : Math.max(fecha - Date.now(), 0);
}

const dormir = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Opciones por petición: cancelación y esquema de la respuesta. */
export interface OpcionesPeticion<T = unknown> {
  signal?: AbortSignal;
  /**
   * Esquema Zod de la respuesta (KC-PT-04-2). Lo pasa quien llama, así cada petición
   * se valida con el suyo y el tipo devuelto sale del esquema.
   */
  esquema?: ZodType<T>;
}

/**
 * Valida la respuesta en la frontera (KC-PT-04-2, cierra PT-F09). Una divergencia con el
 * contrato se registra y se muestra como error con el trace_id de la petición, para
 * encontrarla en los logs del backend; seguir con datos que no cumplen el tipo solo
 * movería el fallo a la vista, donde es más difícil de diagnosticar.
 */
function validar<T>(esquema: ZodType<T>, datos: unknown, ruta: string, traceId: string): T {
  const resultado = esquema.safeParse(datos);
  if (resultado.success) return resultado.data;
  const problemas = resultado.error.issues
    .slice(0, 10)
    .map((p) => `${p.path.join(".") || "(raíz)"}: ${p.message}`);
  // Mismo canal que reportarErrorCliente; el envío al backend llega con KC-PT-11-5.
  console.error("kc.contrato_divergente", { ruta, traceId, problemas });
  throw new ApiError(0, {
    codigo: "RESPUESTA_INVALIDA",
    mensaje: "La respuesta del servidor no tiene el formato esperado.",
    detalle: { problemas },
    trace_id: traceId,
  });
}

async function pedir<T>(
  metodo: string,
  ruta: string,
  cuerpo?: unknown,
  opciones: OpcionesPeticion<T> = {},
  reintento = false,
  reintentos429 = 0,
): Promise<T> {
  const traceId = nuevoTraceId();
  const cabeceras: Record<string, string> = { "X-Trace-Id": traceId };
  if (cuerpo !== undefined) cabeceras["Content-Type"] = "application/json";
  const tokenEnviado = almacen.leer()?.access;
  if (tokenEnviado) cabeceras["Authorization"] = `Bearer ${tokenEnviado}`;

  const r = await enviar(
    `${BASE}${ruta}`,
    {
      method: metodo,
      headers: cabeceras,
      body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
    },
    opciones.signal,
  );

  // El backend rechaza el 429 antes de ejecutar la operación: reintentar no duplica nada.
  if (r.status === 429 && reintentos429 < MAX_REINTENTOS_429) {
    const espera = esperaRetryAfter(r.headers.get("Retry-After"));
    if (espera !== null && espera <= MAX_ESPERA_429_MS) {
      await dormir(espera);
      return pedir<T>(metodo, ruta, cuerpo, opciones, reintento, reintentos429 + 1);
    }
  }

  if (r.status === 401 && !reintento && !ruta.startsWith("/api/v1/auth/")) {
    // Otra petición ya renovó mientras esta viajaba: basta reintentar con el token nuevo.
    const tokenActual = almacen.leer()?.access;
    if (tokenActual && tokenActual !== tokenEnviado)
      return pedir<T>(metodo, ruta, cuerpo, opciones, true);
    if (await renovar()) return pedir<T>(metodo, ruta, cuerpo, opciones, true);
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
  const datos: unknown = r.status === 204 ? undefined : await r.json();
  if (opciones.esquema) return validar(opciones.esquema, datos, ruta, traceId);
  return datos as T;
}

export const api = {
  get: <T>(ruta: string, opciones?: OpcionesPeticion<T>) =>
    pedir<T>("GET", ruta, undefined, opciones),
  post: <T>(ruta: string, cuerpo?: unknown, opciones?: OpcionesPeticion<T>) =>
    pedir<T>("POST", ruta, cuerpo, opciones),
  patch: <T>(ruta: string, cuerpo?: unknown, opciones?: OpcionesPeticion<T>) =>
    pedir<T>("PATCH", ruta, cuerpo, opciones),
  put: <T>(ruta: string, cuerpo?: unknown) => pedir<T>("PUT", ruta, cuerpo),
  del: <T>(ruta: string) => pedir<T>("DELETE", ruta),
};
