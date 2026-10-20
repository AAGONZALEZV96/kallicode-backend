/**
 * Estado de servidor con TanStack Query (KC-PT-04-1, cierra PT-F10).
 *
 * - Cada recurso tiene un hook y una clave en CLAVES. Dos componentes que piden lo mismo
 *   comparten una sola petición y la misma caché (el badge de aprobaciones del Layout y
 *   la vista Aprobaciones; la pestaña Spec y el panel del Gate 1).
 * - Las consultas pasan su AbortSignal al cliente: si dejan de necesitarse (cambio de
 *   filtro, salida de la vista) la petición en curso se cancela.
 * - Cada mutación invalida de una sola vez todas las consultas que dependen del cambio
 *   (invalidar()), así ninguna parte de la pantalla queda mostrando el estado anterior.
 * - Los intervalos de polling salen de GET /organization (KC-PT-04-4, cierra PT-F15):
 *   el backend puede ajustarlos sin desplegar el portal.
 */
import {
  keepPreviousData,
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, ApiError } from "./client";
import type {
  Actividad,
  AprobacionesPendientes,
  AsignacionesLLM,
  Conexiones,
  ConsumoPorTicket,
  ConsumoTicket,
  CriterioGWT,
  Evidencia,
  EventosAuditoria,
  Expediente,
  Hallazgos,
  Impacto,
  Kanban,
  Lineas,
  Notificaciones,
  Organizacion,
  Paginado,
  ProveedoresLLM,
  ResumenTablero,
  Similar,
  Spec,
  TicketDetalle,
  TicketResumen,
  UsageSummary,
} from "./tipos";
import { MAX_TEXTO_PREVIEW } from "./tipos";

export function crearClienteConsultas(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // El cliente ya renueva ante 401 y reintenta los 429: reintentar aquí duplicaría.
        retry: false,
        // Volver a la pestaña del navegador no dispara peticiones; el polling ya refresca.
        refetchOnWindowFocus: false,
        // Navegar entre vistas reutiliza lo recién cargado en vez de pedirlo otra vez.
        staleTime: 10_000,
      },
      mutations: { retry: false },
    },
  });
}

export interface FiltrosTickets {
  q: string;
  etapa: string;
  prioridad: string;
  page: number;
}

export interface FiltrosAuditoria {
  actor: string;
  q: string;
  page: number;
}

/** Pestañas del detalle de ticket y el tipo que devuelve cada una. */
interface Pestanas {
  spec: Spec;
  evidence: Evidencia;
  security: Hallazgos;
  usage: ConsumoTicket;
  activity: Actividad;
}
export type Pestana = keyof Pestanas;

/**
 * Claves de consulta. El primer elemento es la raíz que usa invalidar(): invalidar
 * "tickets" refresca el listado, cada detalle y sus pestañas.
 */
export const CLAVES = {
  tablero: ["tablero"],
  aprobaciones: ["aprobaciones"],
  noLeidas: ["notificaciones", "no-leidas"],
  tickets: (filtros: FiltrosTickets) => ["tickets", "lista", filtros],
  ticket: (numero: string) => ["tickets", "detalle", numero],
  pestana: (numero: string, pestana: Pestana) => ["tickets", "detalle", numero, pestana],
  similares: (texto: string) => ["similares", texto],
  impacto: (texto: string) => ["impacto", texto],
  eventosAuditoria: (filtros: FiltrosAuditoria) => ["auditoria", "eventos", filtros],
  expediente: (numero: string) => ["auditoria", "expediente", numero],
  conexiones: ["conexiones"],
  organizacion: ["organizacion"],
  proveedoresLLM: ["modelos", "proveedores"],
  asignacionesLLM: ["modelos", "asignaciones"],
  resumenConsumo: ["consumo", "resumen"],
  consumoPorTicket: ["consumo", "por-ticket"],
} as const;

type Raiz =
  | "tablero"
  | "aprobaciones"
  | "notificaciones"
  | "tickets"
  | "auditoria"
  | "conexiones"
  | "organizacion"
  | "consumo";

/** Lo que cambia cuando un ticket avanza, se crea o se cancela. */
const TRAS_CAMBIO_DE_TICKET: Raiz[] = [
  "tickets",
  "tablero",
  "aprobaciones",
  "notificaciones",
  "auditoria",
  "consumo",
];

/**
 * Invalida en una sola pasada todas las consultas bajo esas raíces. Se devuelve la
 * promesa para que la mutación siga «pendiente» hasta tener los datos nuevos: así un
 * botón no queda habilitado sobre un estado que ya cambió.
 */
function invalidar(qc: QueryClient, raices: Raiz[]) {
  return qc.invalidateQueries({
    predicate: (q) => raices.some((r) => r === q.queryKey[0]),
  });
}

/* ---------------- intervalos de polling ---------------------------------- */

/** Intervalos de D12 mientras GET /organization no responde, o si falla. */
export const POLLING_POR_DEFECTO = { notificacionesMs: 30_000, tableroMs: 60_000 };
/** Piso de los intervalos: un valor mal configurado en el servidor no satura la API. */
export const POLLING_MINIMO_MS = 5_000;
/**
 * Techo: sobre 2³¹ ms el navegador desborda setInterval y lo dispara cada 1 ms, así que
 * un valor enorme sería peor que uno pequeño.
 */
export const POLLING_MAXIMO_MS = 3_600_000;
/** Refresco del ticket abierto: fijo, /organization no publica este intervalo. */
export const REFRESCO_TICKET_MS = 20_000;

function segundosAMs(segundos: unknown, porDefecto: number): number {
  if (typeof segundos !== "number" || !Number.isFinite(segundos) || segundos <= 0) {
    return porDefecto;
  }
  return Math.min(Math.max(segundos * 1000, POLLING_MINIMO_MS), POLLING_MAXIMO_MS);
}

/** Intervalos que publica el backend en GET /organization (polling, D12). */
export function useIntervalosPolling() {
  const { data } = useOrganizacion();
  return {
    notificacionesMs: segundosAMs(
      data?.polling?.notificaciones_s,
      POLLING_POR_DEFECTO.notificacionesMs,
    ),
    tableroMs: segundosAMs(data?.polling?.tablero_s, POLLING_POR_DEFECTO.tableroMs),
  };
}

/* ---------------- tablero y badges --------------------------------------- */

export interface DatosTablero {
  resumen: ResumenTablero;
  lineas: Lineas;
  kanban: Kanban;
}

/**
 * Tablero completo (§8). Si la carga inicial falla no se sigue sondeando hasta que el
 * usuario reintente (KC-PT-01-5); si falla un refresco se conservan los datos anteriores.
 */
export function useTablero(intervaloMs: number) {
  return useQuery({
    queryKey: CLAVES.tablero,
    queryFn: async ({ signal }): Promise<DatosTablero> => {
      const [resumen, lineas, kanban] = await Promise.all([
        api.get<ResumenTablero>("/api/v1/dashboard/summary", { signal }),
        api.get<Lineas>("/api/v1/dashboard/lines", { signal }),
        api.get<Kanban>("/api/v1/dashboard/kanban", { signal }),
      ]);
      return { resumen, lineas, kanban };
    },
    refetchInterval: (q) => (q.state.data ? intervaloMs : false),
  });
}

interface OpcionesSondeo {
  habilitada?: boolean;
  intervaloMs?: number;
}

export function useAprobaciones({ habilitada = true, intervaloMs }: OpcionesSondeo = {}) {
  return useQuery({
    queryKey: CLAVES.aprobaciones,
    queryFn: ({ signal }) => api.get<AprobacionesPendientes>("/api/v1/approvals/pending", { signal }),
    enabled: habilitada,
    refetchInterval: intervaloMs ?? false,
  });
}

export function useNoLeidas({ habilitada = true, intervaloMs }: OpcionesSondeo = {}) {
  return useQuery({
    queryKey: CLAVES.noLeidas,
    queryFn: ({ signal }) =>
      api.get<Notificaciones>("/api/v1/notifications?page_size=1", { signal }),
    select: (n: any) => n.no_leidas,
    enabled: habilitada,
    refetchInterval: intervaloMs ?? false,
  });
}

/* ---------------- tickets ------------------------------------------------- */

export function useTickets(filtros: FiltrosTickets) {
  return useQuery({
    queryKey: CLAVES.tickets(filtros),
    queryFn: ({ signal }) => {
      const p = new URLSearchParams({ page: String(filtros.page), page_size: "25" });
      if (filtros.q) p.set("q", filtros.q);
      if (filtros.etapa) p.set("etapa", filtros.etapa);
      if (filtros.prioridad) p.set("prioridad", filtros.prioridad);
      return api.get<Paginado<TicketResumen>>(`/api/v1/tickets?${p}`, { signal });
    },
    // Al cambiar de filtro o página se sigue viendo la tabla anterior hasta tener la nueva.
    placeholderData: keepPreviousData,
  });
}

/** Detalle de ticket; se refresca solo mientras está abierto (etapa, gate, stepper). */
export function useTicket(numero: string) {
  return useQuery({
    queryKey: CLAVES.ticket(numero),
    queryFn: ({ signal }) => api.get<TicketDetalle>(`/api/v1/tickets/${numero}`, { signal }),
    refetchInterval: REFRESCO_TICKET_MS,
  });
}

const RUTA_PESTANA: Record<Pestana, string> = {
  spec: "spec",
  evidence: "evidence",
  security: "security",
  usage: "usage",
  activity: "activity?page_size=50",
};

/**
 * Pestaña del detalle; un 404 significa «aún sin datos» y devuelve null, no un error.
 * Solo está montada la pestaña visible, y se refresca al ritmo del ticket: la spec o la
 * evidencia aparecen cuando el pipeline las genera, sin recargar.
 */
export function usePestanaTicket<P extends Pestana>(
  numero: string,
  pestana: P,
  { habilitada = true }: { habilitada?: boolean } = {},
) {
  return useQuery({
    queryKey: CLAVES.pestana(numero, pestana),
    queryFn: async ({ signal }): Promise<Pestanas[P] | null> => {
      try {
        return await api.get<Pestanas[P]>(`/api/v1/tickets/${numero}/${RUTA_PESTANA[pestana]}`, {
          signal,
        });
      } catch (e) {
        if (e instanceof ApiError && e.http === 404) return null;
        throw e;
      }
    },
    enabled: habilitada,
    refetchInterval: REFRESCO_TICKET_MS,
  });
}

export interface DecisionGate {
  accion: "approve" | "request-changes";
  cuerpo: { comentario?: string; alternativa_id?: string };
}

export function useDecidirGate(numero: string, gate: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accion, cuerpo }: DecisionGate) =>
      api.post(`/api/v1/tickets/${numero}/gates/${gate}/${accion}`, cuerpo),
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

export function useCancelarTicket(numero: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (motivo: string) =>
      api.patch(`/api/v1/tickets/${numero}`, { accion: "cancelar", motivo_cancelacion: motivo }),
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

export interface NuevoTicketIn {
  tipo: "bug" | "mejora";
  titulo: string;
  descripcion: string;
  prioridad: string;
  modulo?: string;
}

export function useCrearTicket() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (datos: NuevoTicketIn) =>
      api.post<{ id: string; etapa: string }>("/api/v1/tickets", datos),
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

export interface NuevaFuncionalidadIn {
  nombre: string;
  objetivo: string;
  criterios: CriterioGWT[];
}

export function useCrearFuncionalidad() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (datos: NuevaFuncionalidadIn) =>
      api.post<{ id: string }>("/api/v1/tickets/features", datos),
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

/**
 * Tickets parecidos al texto (§9.4). `texto` llega ya con debounce desde la vista;
 * vacío no consulta. Un fallo (p. ej. 503 sin embeddings) deja la lista vacía.
 */
export function useSimilares(texto: string) {
  return useQuery({
    queryKey: CLAVES.similares(texto),
    queryFn: ({ signal }) =>
      api.post<{ similares: Similar[] }>(
        "/api/v1/tickets/dedup-preview",
        { texto: texto.slice(0, MAX_TEXTO_PREVIEW), k: 3 },
        { signal },
      ),
    select: (r) => r.similares,
    enabled: texto !== "",
  });
}

/** Impacto estimado de una funcionalidad (§9.5); mismas reglas que useSimilares. */
export function useImpacto(texto: string) {
  return useQuery({
    queryKey: CLAVES.impacto(texto),
    queryFn: ({ signal }) =>
      api.post<Impacto>(
        "/api/v1/tickets/impact-preview",
        { texto: texto.slice(0, MAX_TEXTO_PREVIEW) },
        { signal },
      ),
    enabled: texto !== "",
  });
}

/* ---------------- auditoría ---------------------------------------------- */

export function useEventosAuditoria(filtros: FiltrosAuditoria) {
  return useQuery({
    queryKey: CLAVES.eventosAuditoria(filtros),
    queryFn: ({ signal }) => {
      const p = new URLSearchParams({ page: String(filtros.page), page_size: "50" });
      if (filtros.actor) p.set("actor_tipo", filtros.actor);
      if (filtros.q) p.set("q", filtros.q);
      return api.get<EventosAuditoria>(`/api/v1/audit/events?${p}`, { signal });
    },
    placeholderData: keepPreviousData,
  });
}

export function useExpediente(numero: string) {
  return useQuery({
    queryKey: CLAVES.expediente(numero),
    queryFn: ({ signal }) => api.get<Expediente>(`/api/v1/audit/tickets/${numero}`, { signal }),
  });
}

/* ---------------- conexiones y organización ------------------------------ */

export function useConexiones() {
  return useQuery({
    queryKey: CLAVES.conexiones,
    queryFn: ({ signal }) => api.get<Conexiones>("/api/v1/connections", { signal }),
  });
}

export function useOrganizacion() {
  return useQuery({
    queryKey: CLAVES.organizacion,
    queryFn: ({ signal }) => api.get<Organizacion>("/api/v1/organization", { signal }),
  });
}

export function useProbarConexion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<{ estado: string }>(`/api/v1/connections/${id}/test`),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

/** Encender la fábrica activa las líneas: cambian la organización y el tablero. */
export function useEncenderFabrica() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<{ activado: boolean; lineas_activadas: number }>("/api/v1/onboarding/complete"),
    onSuccess: () => invalidar(qc, ["organizacion", "conexiones", "tablero"]),
  });
}

/* ---------------- modelos y consumo -------------------------------------- */

export function useProveedoresLLM() {
  return useQuery({
    queryKey: CLAVES.proveedoresLLM,
    queryFn: ({ signal }) => api.get<ProveedoresLLM>("/api/v1/models/providers", { signal }),
  });
}

export function useAsignacionesLLM() {
  return useQuery({
    queryKey: CLAVES.asignacionesLLM,
    queryFn: ({ signal }) => api.get<AsignacionesLLM>("/api/v1/models/assignments", { signal }),
  });
}

export function useResumenConsumo() {
  return useQuery({
    queryKey: CLAVES.resumenConsumo,
    queryFn: ({ signal }) => api.get<UsageSummary>("/api/v1/usage/summary", { signal }),
  });
}

export function useConsumoPorTicket() {
  return useQuery({
    queryKey: CLAVES.consumoPorTicket,
    queryFn: ({ signal }) =>
      api.get<Paginado<ConsumoPorTicket>>("/api/v1/usage/by-ticket", { signal }),
  });
}

export function useSolicitarUpgrade() {
  return useMutation({
    mutationFn: (planDeseado: string) =>
      api.post<{ mensaje: string }>("/api/v1/plans/upgrade-request", {
        plan_deseado: planDeseado,
      }),
  });
}
