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
  replaceEqualDeep,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { ZodType } from "zod";

import { api, ApiError } from "./client";
import { conservarEnlaces } from "./enlacesEvidencia";
import { parametrosConsulta, type FiltrosUrl } from "./filtrosAuditoria";
import { leerTraza, type TipoTraza } from "./trazas";
import {
  ActividadEsquema,
  AprobacionesEsquema,
  AsignacionesLLMEsquema,
  BandejaEsquema,
  CadenaFirmasEsquema,
  DetallePasoEsquema,
  CambiosGateEsquema,
  ConexionActualizadaEsquema,
  ConexionCreadaEsquema,
  ConexionEliminadaEsquema,
  MapaRamasGuardadoEsquema,
  ConexionesEsquema,
  EstadoGrafoEsquema,
  ConsumoPorTicketEsquema,
  ConsumoTicketEsquema,
  EvidenciaEsquema,
  EventosAuditoriaEsquema,
  ExpedienteEsquema,
  ExportacionCreadaEsquema,
  ExportacionEsquema,
  FabricaEncendidaEsquema,
  FirmaGateEsquema,
  HallazgosEsquema,
  ImpactoEsquema,
  IntegridadEsquema,
  KanbanEsquema,
  LineasEsquema,
  MAX_TEXTO_PREVIEW,
  NotificacionesEsquema,
  NotificacionesLeidasEsquema,
  NotificacionMarcadaEsquema,
  OrganizacionEsquema,
  paginado,
  ProveedoresLLMEsquema,
  ResultadoTestEsquema,
  ResumenTableroEsquema,
  SimilaresEsquema,
  SpecEsquema,
  TicketCreadoEsquema,
  TicketDetalleEsquema,
  TicketResumenEsquema,
  UpgradeSolicitadoEsquema,
  UsageSummaryEsquema,
  UsuarioOrgEsquema,
  VisualizadoresEsquema,
  type Actividad,
  type CambiosGate,
  type CambiosGateIn,
  type ConexionIn,
  type ConexionPatchIn,
  type ExportacionIn,
  type EstadoGrafo,
  type MapaRamasIn,
  type ConsumoTicket,
  type Evidencia,
  type FirmaGate,
  type FirmaGateIn,
  type Hallazgos,
  type Kanban,
  type LeidaIn,
  type Lineas,
  type NuevaFuncionalidadIn,
  type NuevoTicketIn,
  type PreviewIn,
  type ResumenTablero,
  type Spec,
  type TicketDetalle,
  type TicketPatchIn,
  type UpgradeIn,
  type Visualizador,
  type WatcherIn,
} from "./tipos";

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

/** Filtros saneados del explorador de auditoría (filtrosAuditoria.ts) y la página. */
export interface FiltrosAuditoria extends FiltrosUrl {
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

const ESQUEMA_PESTANA: { [P in Pestana]: ZodType<Pestanas[P]> } = {
  spec: SpecEsquema,
  evidence: EvidenciaEsquema,
  security: HallazgosEsquema,
  usage: ConsumoTicketEsquema,
  activity: ActividadEsquema,
};

/**
 * Claves de consulta. El primer elemento es la raíz que usa invalidar(): invalidar
 * "tickets" refresca el listado, cada detalle y sus pestañas.
 */
export const CLAVES = {
  tablero: ["tablero"],
  aprobaciones: ["aprobaciones"],
  noLeidas: ["notificaciones", "no-leidas"],
  bandeja: ["notificaciones", "bandeja"],
  tickets: (filtros: FiltrosTickets) => ["tickets", "lista", filtros],
  ticket: (numero: string) => ["tickets", "detalle", numero],
  // Bajo «tickets»: al firmar o pedir cambios se invalida con el resto del ticket.
  firmas: (numero: string) => ["tickets", "detalle", numero, "firmas"],
  usuarios: (texto: string) => ["usuarios", texto],
  pestana: (numero: string, pestana: Pestana, pagina = 1) => [
    "tickets",
    "detalle",
    numero,
    pestana,
    pagina,
  ],
  similares: (texto: string) => ["similares", texto],
  impacto: (texto: string) => ["impacto", texto],
  eventosAuditoria: (filtros: FiltrosAuditoria) => ["auditoria", "eventos", filtros],
  expediente: (numero: string) => ["auditoria", "expediente", numero],
  exportacion: (id: string) => ["auditoria", "exportacion", id],
  integridad: (numero: string) => ["auditoria", "integridad", numero],
  paso: (stepId: string) => ["auditoria", "paso", stepId],
  traza: (stepId: string, tipo: TipoTraza) => ["auditoria", "paso", stepId, "traza", tipo],
  conexiones: ["conexiones"],
  // Bajo «conexiones»: lo que cambie las conexiones también refresca el estado del grafo.
  estadoGrafo: ["conexiones", "grafo"],
  organizacion: ["organizacion"],
  proveedoresLLM: ["modelos", "proveedores"],
  asignacionesLLM: ["modelos", "asignaciones"],
  resumenConsumo: ["consumo", "resumen"],
  consumoPorTicket: (pagina: number, orden: string | null) => [
    "consumo",
    "por-ticket",
    pagina,
    orden,
  ],
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
        api.get("/api/v1/dashboard/summary", { signal, esquema: ResumenTableroEsquema }),
        api.get("/api/v1/dashboard/lines", { signal, esquema: LineasEsquema }),
        api.get("/api/v1/dashboard/kanban", { signal, esquema: KanbanEsquema }),
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
    queryFn: ({ signal }) =>
      api.get("/api/v1/approvals/pending", { signal, esquema: AprobacionesEsquema }),
    enabled: habilitada,
    refetchInterval: intervaloMs,
  });
}

export function useNoLeidas({ habilitada = true, intervaloMs }: OpcionesSondeo = {}) {
  return useQuery({
    queryKey: CLAVES.noLeidas,
    queryFn: ({ signal }) =>
      api.get("/api/v1/notifications?page_size=1", { signal, esquema: NotificacionesEsquema }),
    select: (n) => n.no_leidas,
    enabled: habilitada,
    refetchInterval: intervaloMs,
  });
}

/** Notificaciones que muestra la bandeja: las más recientes primero. */
export const TAMANO_BANDEJA = 20;

/**
 * Bandeja de notificaciones (KC-PT-06-1). Se consulta solo con la bandeja abierta; mientras
 * sigue abierta se refresca al intervalo de notificaciones, igual que el contador.
 */
export function useBandeja({ habilitada = true, intervaloMs }: OpcionesSondeo = {}) {
  return useQuery({
    queryKey: CLAVES.bandeja,
    queryFn: ({ signal }) =>
      api.get(`/api/v1/notifications?page_size=${TAMANO_BANDEJA}`, {
        signal,
        esquema: BandejaEsquema,
      }),
    enabled: habilitada,
    refetchInterval: intervaloMs,
  });
}

/** Marca una notificación como leída o no leída; el contador y la bandeja se refrescan juntos. */
export function useMarcarNotificacion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, leida }: { id: string } & LeidaIn) =>
      api.patch(`/api/v1/notifications/${encodeURIComponent(id)}`, { leida } satisfies LeidaIn, {
        esquema: NotificacionMarcadaEsquema,
      }),
    onSuccess: () => invalidar(qc, ["notificaciones"]),
  });
}

export function useLeerTodas() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post("/api/v1/notifications/read-all", undefined, {
        esquema: NotificacionesLeidasEsquema,
      }),
    onSuccess: () => invalidar(qc, ["notificaciones"]),
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
      return api.get(`/api/v1/tickets?${p}`, { signal, esquema: paginado(TicketResumenEsquema) });
    },
    // Al cambiar de filtro o página se sigue viendo la tabla anterior hasta tener la nueva.
    placeholderData: keepPreviousData,
  });
}

/** Detalle de ticket; se refresca solo mientras está abierto (etapa, gate, stepper). */
export function useTicket(numero: string) {
  return useQuery({
    queryKey: CLAVES.ticket(numero),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/tickets/${numero}`, { signal, esquema: TicketDetalleEsquema }),
    refetchInterval: REFRESCO_TICKET_MS,
  });
}

/**
 * Cadena de firmas de los gates (KC-PT-06-4). Va al ritmo del ticket: si otra persona
 * firma, aparece sin recargar; al firmar desde este portal se invalida enseguida.
 */
export function useCadenaFirmas(numero: string) {
  return useQuery({
    queryKey: CLAVES.firmas(numero),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/tickets/${numero}/gates`, { signal, esquema: CadenaFirmasEsquema }),
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
  { habilitada = true, pagina = 1 }: { habilitada?: boolean; pagina?: number } = {},
) {
  return useQuery({
    queryKey: CLAVES.pestana(numero, pestana, pagina),
    queryFn: async ({ signal }): Promise<Pestanas[P] | null> => {
      const ruta = `/api/v1/tickets/${numero}/${RUTA_PESTANA[pestana]}`;
      const conPagina =
        pagina > 1 ? `${ruta}${ruta.includes("?") ? "&" : "?"}page=${pagina}` : ruta;
      try {
        return await api.get(conPagina, {
          signal,
          esquema: ESQUEMA_PESTANA[pestana],
        });
      } catch (e) {
        if (e instanceof ApiError && e.http === 404) return null;
        throw e;
      }
    },
    enabled: habilitada,
    refetchInterval: REFRESCO_TICKET_MS,
    // Evidencia: cada respuesta trae firmas nuevas; se conservan las vigentes (KC-PT-05-2).
    structuralSharing: (previos, nuevos) =>
      pestana === "evidence"
        ? conservarEnlaces(previos, nuevos)
        : replaceEqualDeep(previos, nuevos),
    // Al cambiar de página se sigue viendo la anterior (PT-F16), pero nunca datos de otro
    // ticket u otra pestaña.
    placeholderData: (previos, consultaPrevia) =>
      consultaPrevia?.queryKey[2] === numero && consultaPrevia.queryKey[3] === pestana
        ? previos
        : undefined,
  });
}

/** Cuerpos tipados con el contrato: FirmaIn al aprobar, CambiosIn al pedir cambios. */
export type DecisionGate =
  { accion: "approve"; cuerpo: FirmaGateIn } | { accion: "request-changes"; cuerpo: CambiosGateIn };

/** Lo que devuelve la decisión: el sello si se firmó, la etapa de vuelta si se pidieron cambios. */
export type ResultadoGate =
  { accion: "approve"; firma: FirmaGate } | { accion: "request-changes"; cambios: CambiosGate };

export function useDecidirGate(numero: string, gate: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (decision: DecisionGate): Promise<ResultadoGate> => {
      const ruta = `/api/v1/tickets/${numero}/gates/${gate}/${decision.accion}`;
      return decision.accion === "approve"
        ? {
            accion: "approve",
            firma: await api.post(ruta, decision.cuerpo, { esquema: FirmaGateEsquema }),
          }
        : {
            accion: "request-changes",
            cambios: await api.post(ruta, decision.cuerpo, { esquema: CambiosGateEsquema }),
          };
    },
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

/** Máximo de visualizadores por ticket (tickets.add_watcher: LIMITE_VISUALIZADORES). */
export const MAX_VISUALIZADORES = 25;

/**
 * Personas activas de la organización que coinciden con `texto` (nombre o correo), para
 * elegir visualizadores (KC-PT-06-2). Solo la piden los roles que pueden listar usuarios.
 */
export function useUsuariosActivos(texto: string, habilitada: boolean) {
  return useQuery({
    queryKey: CLAVES.usuarios(texto),
    queryFn: ({ signal }) => {
      const p = new URLSearchParams({ estado: "activo", page_size: "20" });
      if (texto) p.set("q", texto);
      return api.get(`/api/v1/users?${p}`, { signal, esquema: paginado(UsuarioOrgEsquema) });
    },
    enabled: habilitada,
    placeholderData: keepPreviousData,
  });
}

/** El backend devuelve la lista que queda: se escribe en el detalle sin otra petición. */
function conVisualizadores(qc: QueryClient, numero: string, visualizadores: Visualizador[]) {
  qc.setQueryData<TicketDetalle>(CLAVES.ticket(numero), (previo) =>
    previo ? { ...previo, visualizadores } : previo,
  );
}

export function useAgregarVisualizador(numero: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => {
      const cuerpo: WatcherIn = { user_id: userId };
      return api.post(`/api/v1/tickets/${numero}/watchers`, cuerpo, {
        esquema: VisualizadoresEsquema,
      });
    },
    onSuccess: (r) => conVisualizadores(qc, numero, r.visualizadores),
  });
}

export function useQuitarVisualizador(numero: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.del(`/api/v1/tickets/${numero}/watchers/${encodeURIComponent(userId)}`, {
        esquema: VisualizadoresEsquema,
      }),
    onSuccess: (r) => conVisualizadores(qc, numero, r.visualizadores),
  });
}

export function useCancelarTicket(numero: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (motivo: string) => {
      const cuerpo: TicketPatchIn = { accion: "cancelar", motivo_cancelacion: motivo };
      return api.patch(`/api/v1/tickets/${numero}`, cuerpo);
    },
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

export function useCrearTicket() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (datos: NuevoTicketIn) =>
      api.post("/api/v1/tickets", datos, { esquema: TicketCreadoEsquema }),
    onSuccess: () => invalidar(qc, TRAS_CAMBIO_DE_TICKET),
  });
}

export function useCrearFuncionalidad() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (datos: NuevaFuncionalidadIn) =>
      api.post("/api/v1/tickets/features", datos, { esquema: TicketCreadoEsquema }),
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
    queryFn: ({ signal }) => {
      const cuerpo: PreviewIn = { texto: texto.slice(0, MAX_TEXTO_PREVIEW), k: 3 };
      return api.post("/api/v1/tickets/dedup-preview", cuerpo, {
        signal,
        esquema: SimilaresEsquema,
      });
    },
    select: (r) => r.similares,
    enabled: texto !== "",
  });
}

/** Impacto estimado de una funcionalidad (§9.5); mismas reglas que useSimilares. */
export function useImpacto(texto: string) {
  return useQuery({
    queryKey: CLAVES.impacto(texto),
    queryFn: ({ signal }) => {
      const cuerpo: PreviewIn = { texto: texto.slice(0, MAX_TEXTO_PREVIEW) };
      return api.post("/api/v1/tickets/impact-preview", cuerpo, {
        signal,
        esquema: ImpactoEsquema,
      });
    },
    enabled: texto !== "",
  });
}

/* ---------------- auditoría ---------------------------------------------- */

/** Con `habilitada: false` (p. ej. un rango de fechas inválido) no consulta. */
export function useEventosAuditoria(filtros: FiltrosAuditoria, habilitada = true) {
  return useQuery({
    queryKey: CLAVES.eventosAuditoria(filtros),
    queryFn: ({ signal }) => {
      const p = parametrosConsulta(filtros, filtros.page);
      return api.get(`/api/v1/audit/events?${p}`, { signal, esquema: EventosAuditoriaEsquema });
    },
    placeholderData: keepPreviousData,
    enabled: habilitada,
  });
}

/** Pide una exportación (CSV o PDF); el worker la procesa en segundo plano (KC-PT-08-3). */
export function useCrearExportacion() {
  return useMutation({
    mutationFn: (pedido: ExportacionIn) =>
      api.post("/api/v1/audit/exports", pedido, { esquema: ExportacionCreadaEsquema }),
  });
}

/** Mientras se sondea una exportación pendiente. */
export const INTERVALO_EXPORTACION_MS = 1500;

/** Estado de una exportación: se vuelve a consultar hasta que queda lista o fallida. */
export function useEstadoExportacion(id: string | null) {
  return useQuery({
    queryKey: CLAVES.exportacion(id ?? ""),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/audit/exports/${encodeURIComponent(id!)}`, {
        signal,
        esquema: ExportacionEsquema,
      }),
    enabled: id !== null,
    refetchInterval: (consulta) => {
      const estado = consulta.state.data?.estado;
      return estado === "lista" || estado === "fallida" ? false : INTERVALO_EXPORTACION_MS;
    },
  });
}

export function useExpediente(numero: string) {
  return useQuery({
    queryKey: CLAVES.expediente(numero),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/audit/tickets/${encodeURIComponent(numero)}`, {
        signal,
        esquema: ExpedienteEsquema,
      }),
  });
}

/**
 * Vuelve a verificar los sellos del tramo del ticket (GET /audit/integrity?ticket_id=…,
 * KC-PT-08-4). A pedido: recorrer la cadena tiene costo, así que no se repite sola.
 * Sin desde/hasta abarca todos los eventos del ticket, también los llegados tras abrir la vista.
 * Después se vuelve a pedir el expediente: si un evento cambió o llegó uno nuevo, la lista
 * muestra lo que hay ahora y no lo que se cargó al entrar.
 */
export function useVerificarIntegridad(numero: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: CLAVES.integridad(numero),
    mutationFn: () =>
      api.get(`/api/v1/audit/integrity?${new URLSearchParams({ ticket_id: numero })}`, {
        esquema: IntegridadEsquema,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: CLAVES.expediente(numero) }),
  });
}

/**
 * Detalle de un paso LLM para el panel de inspección (KC-PT-08-2). Se pide al abrir el panel y
 * otra vez en cada apertura: las URLs de las trazas vencen a los 10 minutos.
 */
export function useDetallePaso(stepId: string | null) {
  return useQuery({
    queryKey: CLAVES.paso(stepId ?? ""),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/audit/steps/${encodeURIComponent(stepId!)}`, {
        signal,
        esquema: DetallePasoEsquema,
      }),
    enabled: stepId !== null,
    staleTime: 0,
  });
}

/**
 * Contenido de una traza (entrada o salida) leído de su URL firmada. La traza de un paso no
 * cambia (su ruta lleva el step_id), así que no se vuelve a pedir mientras esté en memoria.
 */
export function useTrazaPaso(stepId: string, tipo: TipoTraza, url: string | null) {
  return useQuery({
    queryKey: CLAVES.traza(stepId, tipo),
    queryFn: ({ signal }) => leerTraza(url!, signal),
    enabled: url !== null,
    staleTime: Infinity,
  });
}

/* ---------------- conexiones y organización ------------------------------ */

export function useConexiones() {
  return useQuery({
    queryKey: CLAVES.conexiones,
    queryFn: ({ signal }) => api.get("/api/v1/connections", { signal, esquema: ConexionesEsquema }),
  });
}

/** Avance del grafo de código del onboarding (KC-PT-07-1, paso 5 del asistente). */
/** Mientras el grafo no llega al 100 % se vuelve a consultar cada 15 s (KC-PT-07-5). */
export const INTERVALO_GRAFO_MS = 15_000;
export const intervaloDelGrafo = (grafo: EstadoGrafo | undefined) =>
  grafo && grafo.progreso < 1 ? INTERVALO_GRAFO_MS : false;

export function useEstadoGrafo() {
  return useQuery({
    queryKey: CLAVES.estadoGrafo,
    queryFn: ({ signal }) =>
      api.get("/api/v1/onboarding/codemapping/status", { signal, esquema: EstadoGrafoEsquema }),
    refetchInterval: (consulta) => intervaloDelGrafo(consulta.state.data),
  });
}

export function useOrganizacion() {
  return useQuery({
    queryKey: CLAVES.organizacion,
    queryFn: ({ signal }) =>
      api.get("/api/v1/organization", { signal, esquema: OrganizacionEsquema }),
  });
}

export function useProbarConexion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post(`/api/v1/connections/${id}/test`, undefined, { esquema: ResultadoTestEsquema }),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

/**
 * CRUD de conexiones del asistente (KC-PT-07-2). Cada cambio refresca las conexiones y el
 * estado del grafo, que cuelga de la misma raíz.
 */
export function useCrearConexion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cuerpo: ConexionIn) =>
      api.post("/api/v1/connections", cuerpo, { esquema: ConexionCreadaEsquema }),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

export function useRenombrarConexion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, nombre }: { id: string; nombre: string }) => {
      const cuerpo: ConexionPatchIn = { nombre };
      return api.patch(`/api/v1/connections/${encodeURIComponent(id)}`, cuerpo, {
        esquema: ConexionActualizadaEsquema,
      });
    },
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

/** Rotación de credenciales (KC-PT-07-4): el backend las prueba antes de guardarlas. */
export function useRotarCredenciales() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...cuerpo
    }: { id: string } & Required<Pick<ConexionPatchIn, "config" | "credenciales">>) =>
      api.patch(`/api/v1/connections/${encodeURIComponent(id)}`, cuerpo, {
        esquema: ConexionActualizadaEsquema,
      }),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

export function useEliminarConexion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.del(`/api/v1/connections/${encodeURIComponent(id)}`, {
        esquema: ConexionEliminadaEsquema,
      }),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

/** Mapa de ramas (KC-PT-07-3): vive en GET /connections, que se refresca al guardarlo. */
export function useGuardarMapaRamas() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cuerpo: MapaRamasIn) =>
      api.put("/api/v1/connections/branch-map", cuerpo, { esquema: MapaRamasGuardadoEsquema }),
    onSuccess: () => invalidar(qc, ["conexiones"]),
  });
}

/** Encender la fábrica activa las líneas: cambian la organización y el tablero. */
export function useEncenderFabrica() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post("/api/v1/onboarding/complete", undefined, { esquema: FabricaEncendidaEsquema }),
    onSuccess: () => invalidar(qc, ["organizacion", "conexiones", "tablero"]),
    // Un 409 ONBOARDING_INCOMPLETO dice que los datos cambiaron (p. ej. en otra pestaña).
    onError: () => invalidar(qc, ["organizacion", "conexiones"]),
  });
}

/* ---------------- modelos y consumo -------------------------------------- */

export function useProveedoresLLM() {
  return useQuery({
    queryKey: CLAVES.proveedoresLLM,
    queryFn: ({ signal }) =>
      api.get("/api/v1/models/providers", { signal, esquema: ProveedoresLLMEsquema }),
  });
}

export function useAsignacionesLLM() {
  return useQuery({
    queryKey: CLAVES.asignacionesLLM,
    queryFn: ({ signal }) =>
      api.get("/api/v1/models/assignments", { signal, esquema: AsignacionesLLMEsquema }),
  });
}

export function useResumenConsumo() {
  return useQuery({
    queryKey: CLAVES.resumenConsumo,
    queryFn: ({ signal }) =>
      api.get("/api/v1/usage/summary", { signal, esquema: UsageSummaryEsquema }),
  });
}

/** Columnas por las que se ordena el consumo por ticket (usage._SORT_USAGE). */
export const ORDENES_CONSUMO = ["loc_netas", "tokens", "tiempo_e2e"] as const;
export type OrdenConsumo = (typeof ORDENES_CONSUMO)[number];

/** «-tokens» o «tiempo_e2e» si es un orden válido; null (el del backend) si no. */
export function sanearOrdenConsumo(valor: string | null): string | null {
  const campo = valor?.replace(/^-/, "");
  return campo && (ORDENES_CONSUMO as readonly string[]).includes(campo) ? valor : null;
}

/** Consumo por ticket del ciclo (KC-PT-09-2): `orden` es el sort del backend («-tiempo_e2e»). */
export function useConsumoPorTicket(pagina: number, orden: string | null = null) {
  const p = new URLSearchParams({ page: String(pagina) });
  if (orden) p.set("sort", orden);
  return useQuery({
    queryKey: CLAVES.consumoPorTicket(pagina, orden),
    queryFn: ({ signal }) =>
      api.get(`/api/v1/usage/by-ticket?${p}`, {
        signal,
        esquema: paginado(ConsumoPorTicketEsquema),
      }),
    placeholderData: keepPreviousData,
  });
}

export function useSolicitarUpgrade() {
  return useMutation({
    mutationFn: (planDeseado: string) => {
      const cuerpo: UpgradeIn = { plan_deseado: planDeseado };
      return api.post("/api/v1/plans/upgrade-request", cuerpo, {
        esquema: UpgradeSolicitadoEsquema,
      });
    },
  });
}
