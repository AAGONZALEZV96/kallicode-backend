/**
 * Telemetría del portal (KC-PT-01-3, KC-PT-11-5): errores de cliente agrupados y Web Vitals,
 * enviados en lotes a POST /api/v1/telemetry/client.
 *
 * - Qué se reporta: excepciones no capturadas (error), promesas rechazadas sin manejar
 *   (rechazo), lo que atrapa un <LimiteError> (limite) y respuestas que no cumplen el contrato
 *   de la API (contrato). Más las Web Vitals de la visita (metricas/webVitals.ts).
 * - Se agrupa por firma (tipo, mensaje sin números ni identificadores, y origen): diez veces el
 *   mismo error son un grupo con «veces: 10», no diez envíos.
 * - Se envía a los 5 s del primer evento y al ocultar o cerrar la pestaña (fetch keepalive, que
 *   sobrevive al cierre). Un fallo del envío se ignora: la telemetría nunca rompe el portal.
 * - Sin datos del cliente: la ruta va sin identificadores y el mensaje recortado; el backend
 *   además quita correos y tokens antes de registrarlo.
 */
import { ApiError, enviarSinEsperar } from "./client";
import type { TelemetriaClienteIn } from "./tipos";

export interface ErrorCliente {
  /** Identificador corto que la pantalla de recuperación muestra al usuario. */
  id: string;
  nivel: "raiz" | "ruta";
  ruta: string;
  mensaje: string;
  /** trace_id del backend si el error vino de la API. */
  traceId?: string;
  pilaComponentes?: string;
}

export type TipoErrorCliente = "error" | "rechazo" | "limite" | "contrato";
type Vital = NonNullable<TelemetriaClienteIn["vitals"]>[number];
type GrupoError = NonNullable<TelemetriaClienteIn["errores"]>[number];

/** Topes del backend (TelemetriaClienteIn): grupos y métricas por lote, largo de los textos. */
const MAX_GRUPOS_POR_LOTE = 20;
const MAX_VITALS_POR_LOTE = 10;
const MAX_MENSAJE = 300;
const MAX_RUTA = 200;
const MAX_VECES = 1000;
/** Grupos distintos que se juntan por página: un error en bucle no llena la memoria. */
const MAX_GRUPOS_PENDIENTES = 50;
export const ESPERA_ENVIO_MS = 5000;

/** Errores que no dicen nada útil: el del ResizeObserver es benigno; «Script error.» es de otro origen. */
const IGNORADOS = [/^ResizeObserver loop/, /^Script error\.?$/];

const grupos = new Map<string, GrupoError>();
const vitals: Vital[] = [];
let temporizador: ReturnType<typeof setTimeout> | undefined;

export function nuevoIdError(): string {
  return `err_${crypto.randomUUID().slice(0, 8)}`;
}

export function describirError(
  error: unknown,
  datos: Omit<ErrorCliente, "mensaje" | "traceId">,
): ErrorCliente {
  return {
    ...datos,
    mensaje: error instanceof Error ? error.message : String(error),
    traceId: error instanceof ApiError ? error.traceId : undefined,
  };
}

/** La ruta sin datos del cliente: /tickets/KC-1003 → /tickets/:numero. */
export function rutaGenerica(pathname: string): string {
  return pathname.replace(/\/KC-\d+(?=\/|$)/gi, "/:numero").slice(0, MAX_RUTA);
}

/** Firma del grupo: tipo, mensaje sin números ni identificadores, y origen (FNV-1a, 32 bits). */
export function firmaDe(tipo: TipoErrorCliente, mensaje: string, origen = ""): string {
  const normalizado = `${tipo}|${mensaje.replace(/[0-9a-f]{8,}|\d+/gi, "#")}|${origen}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < normalizado.length; i++) {
    h ^= normalizado.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${tipo}-${h.toString(16).padStart(8, "0")}`;
}

/** Suma un error a su grupo y programa el envío. */
export function registrarError(
  tipo: TipoErrorCliente,
  mensaje: string,
  { origen = "", traceId }: { origen?: string; traceId?: string } = {},
) {
  if (IGNORADOS.some((patron) => patron.test(mensaje))) return;
  const firma = firmaDe(tipo, mensaje, origen);
  const grupo = grupos.get(firma);
  if (grupo) {
    grupo.veces = Math.min(grupo.veces + 1, MAX_VECES);
  } else {
    if (grupos.size >= MAX_GRUPOS_PENDIENTES) return;
    grupos.set(firma, {
      firma,
      tipo,
      mensaje: mensaje.slice(0, MAX_MENSAJE),
      ruta: rutaGenerica(window.location.pathname),
      veces: 1,
      trace_id: traceId?.slice(0, 64) ?? null,
    });
  }
  programarEnvio();
}

export function registrarVital(vital: Vital) {
  vitals.push({ ...vital, ruta: rutaGenerica(vital.ruta) });
  programarEnvio();
}

/** Lo que atrapó un <LimiteError> (KC-PT-01-3): queda en la consola y va al backend. */
export function reportarErrorCliente(evento: ErrorCliente): void {
  console.error("kc.error_cliente", evento);
  const origen =
    evento.pilaComponentes
      ?.split("\n")
      .find((l) => l.trim())
      ?.trim() ?? "";
  registrarError("limite", evento.mensaje, { origen, traceId: evento.traceId });
}

function programarEnvio() {
  // Al ocultarse la pestaña ya no corren temporizadores: se envía en el momento.
  if (document.visibilityState === "hidden") {
    enviarPendientes();
    return;
  }
  temporizador ??= setTimeout(enviarPendientes, ESPERA_ENVIO_MS);
}

/** Envía lo juntado, en lotes que respetan los topes del backend. */
export function enviarPendientes() {
  clearTimeout(temporizador);
  temporizador = undefined;
  while (grupos.size > 0 || vitals.length > 0) {
    const errores = [...grupos.values()].slice(0, MAX_GRUPOS_POR_LOTE);
    for (const e of errores) grupos.delete(e.firma);
    const lote: TelemetriaClienteIn = { errores, vitals: vitals.splice(0, MAX_VITALS_POR_LOTE) };
    enviarSinEsperar("/api/v1/telemetry/client", lote);
  }
}

let iniciada = false;

/** Escucha los errores no manejados y envía lo pendiente al ocultar o cerrar la pestaña. */
export function iniciarTelemetria() {
  if (iniciada) return;
  iniciada = true;
  window.addEventListener("error", (e) => {
    const origen = e.filename ? `${e.filename.split("/").pop()}:${e.lineno}` : "";
    registrarError("error", e.message || String(e.error), { origen });
  });
  window.addEventListener("unhandledrejection", (e) => {
    const motivo: unknown = e.reason;
    const mensaje = motivo instanceof Error ? motivo.message : String(motivo);
    registrarError("rechazo", mensaje, {
      traceId: motivo instanceof ApiError ? motivo.traceId : undefined,
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") enviarPendientes();
  });
  window.addEventListener("pagehide", enviarPendientes);
}

/** Solo para pruebas: vacía lo pendiente sin enviarlo. */
export function reiniciarTelemetria() {
  clearTimeout(temporizador);
  temporizador = undefined;
  grupos.clear();
  vitals.length = 0;
}
