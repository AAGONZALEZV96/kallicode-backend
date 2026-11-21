/**
 * Filtros del explorador de auditoría (KC-PT-08-5): los de GET /audit/events (audit.eventos).
 *
 * En la URL van con nombres cortos y valores legibles (`?actor=agente&orden=antiguos`); aquí
 * se sanean (un enlace viejo o editado a mano no debe provocar un 422) y se traducen a los
 * parámetros del backend.
 */

export const CLAVES_FILTRO = ["q", "actor", "ticket", "etapa", "desde", "hasta", "orden"] as const;
export type ClaveFiltro = (typeof CLAVES_FILTRO)[number];
export type FiltrosUrl = Record<ClaveFiltro, string>;

/** audit_events.actor_tipo (ae_actor_chk). */
export const ACTORES = ["humano", "agente", "sistema"] as const;
/** Etapas del pipeline que registran eventos. */
export const ETAPAS_AUDITORIA = [
  "triage",
  "design",
  "build",
  "qa",
  "security",
  "deploy",
  "produccion",
] as const;
/** Orden visible → `sort` del backend (audit._SORT_AUDIT); sin orden, los más recientes. */
export const ORDENES = {
  antiguos: "creado_en",
  etapa: "etapa",
  actor: "actor_tipo",
} as const;
/** audit.eventos responde RANGO_EXCESIVO si el rango supera 92 días. */
export const RANGO_MAXIMO_DIAS = 92;
/** Largos máximos que acepta el backend (q: max_length=200). */
const LARGO_MAXIMO = { q: 200, ticket: 40 } as const;

const FECHA = /^\d{4}-\d{2}-\d{2}$/;
const esFecha = (v: string) => FECHA.test(v) && !Number.isNaN(new Date(`${v}T00:00:00`).getTime());
const incluido = (lista: readonly string[], v: string) => lista.includes(v);

/** Lo que vino en la URL, sin valores que el backend rechazaría. */
export function sanear(valores: FiltrosUrl): FiltrosUrl {
  return {
    q: valores.q.trim().slice(0, LARGO_MAXIMO.q),
    ticket: valores.ticket.trim().slice(0, LARGO_MAXIMO.ticket),
    actor: incluido(ACTORES, valores.actor) ? valores.actor : "",
    etapa: incluido(ETAPAS_AUDITORIA, valores.etapa) ? valores.etapa : "",
    desde: esFecha(valores.desde) ? valores.desde : "",
    hasta: esFecha(valores.hasta) ? valores.hasta : "",
    orden: valores.orden in ORDENES ? valores.orden : "",
  };
}

export const hayFiltros = (f: FiltrosUrl) => CLAVES_FILTRO.some((k) => f[k] !== "");

/** Inicio y fin de los días elegidos, en la zona del navegador. */
const inicioDelDia = (fecha: string) => new Date(`${fecha}T00:00:00`);
const finDelDia = (fecha: string) => new Date(`${fecha}T23:59:59.999`);

/** Un rango que el backend rechazaría o que no tiene sentido; undefined si está bien. */
export function errorDeRango(desde: string, hasta: string): "invertido" | "excesivo" | undefined {
  if (!desde || !hasta) return undefined;
  // Días de calendario en UTC: un cambio de horario en el medio no suma ni resta horas.
  const dias = (Date.parse(`${hasta}T00:00:00Z`) - Date.parse(`${desde}T00:00:00Z`)) / 86_400_000;
  if (dias < 0) return "invertido";
  if (dias > RANGO_MAXIMO_DIAS) return "excesivo";
  return undefined;
}

/** Parámetros de GET /audit/events para estos filtros (ya saneados) y esta página. */
export function parametrosConsulta(f: FiltrosUrl, page: number, pageSize = 50): URLSearchParams {
  const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (f.q) p.set("q", f.q);
  if (f.actor) p.set("actor_tipo", f.actor);
  if (f.ticket) p.set("ticket_id", f.ticket);
  if (f.etapa) p.set("etapa", f.etapa);
  // Días completos: el «hasta» incluye todo ese día.
  if (f.desde) p.set("desde", inicioDelDia(f.desde).toISOString());
  if (f.hasta) p.set("hasta", finDelDia(f.hasta).toISOString());
  if (f.orden in ORDENES) p.set("sort", ORDENES[f.orden as keyof typeof ORDENES]);
  return p;
}
