/**
 * Tipos de los contratos del backend y esquemas Zod en la frontera.
 * Cumple con PT-F09 y PT-F19: validación estricta de esquemas de API y corrección de divergencias.
 */
import { z } from "zod";

export type Etapa =
  | "triage"
  | "design"
  | "build"
  | "qa"
  | "security"
  | "deploy"
  | "produccion"
  | "en_cola_por_cuota"
  | "cancelado"
  | "cerrado_duplicado";
export type Prioridad = "alta" | "media" | "baja";

export const MAX_TEXTO_PREVIEW = 2000;
export type TipoTicket = "bug" | "mejora" | "funcionalidad" | "seguridad";

export const ROLES = ["owner", "admin", "architect", "approver", "member", "viewer"] as const;
export type Rol = (typeof ROLES)[number];
export const esRol = (valor: unknown): valor is Rol => ROLES.some((r) => r === valor);

export type EstadoJob =
  | "pendiente_asignacion"
  | "triage"
  | "design"
  | "gate1"
  | "build"
  | "qa"
  | "gate2"
  | "security"
  | "deploy_preflight"
  | "gate3"
  | "deploy"
  | "produccion"
  | "pausado_por_cuota"
  | "escalado_humano"
  | "cancelado"
  | "cerrado_duplicado";

// --- ESQUEMAS ZOD (PT-F09, PT-F19) ---

export const OrganizacionSchema = z.object({
  id: z.string(),
  nombre: z.string(),
  idioma: z.string(),
  timezone: z.string(),
  despliegue: z.string(),
  fabrica_activa: z.boolean(),
  plan: z.object({
    codigo: z.string(),
    nombre: z.string(),
    lineas: z.number().nullable(),
    loc_mes: z.number().nullable(),
    renueva_el: z.string(),
  }).nullable(),
  polling: z.object({ notificaciones_s: z.number(), tablero_s: z.number() }),
});
export type Organizacion = z.infer<typeof OrganizacionSchema>;

export const TicketDetalleSchema = z.object({
  id: z.string(),
  titulo: z.string(),
  tipo: z.enum(["bug", "mejora", "funcionalidad", "seguridad"] as const),
  prioridad: z.enum(["alta", "media", "baja"] as const),
  origen: z.string(),
  reportado_por: z.object({ id: z.string(), nombre: z.string() }).nullable(),
  etapa: z.string(),
  gate_pendiente: z.number().nullable(),
  pipeline: z.array(z.object({ 
    etapa: z.string(), 
    estado: z.enum(["done", "actual", "pendiente"] as const), 
    gate: z.number().nullable() 
  })),
  detalle: z.object({
    linea: z.number().nullable(),
    radio_impacto: z.array(z.string()),
    estimacion: z.string().nullable(),
    pr_url: z.string().nullable(),
  }),
  visualizadores: z.array(z.object({ id: z.string(), nombre: z.string() })),
});
export type TicketDetalle = z.infer<typeof TicketDetalleSchema>;

export const EvidenciaSchema = z.object({
  corrida: z.number(),
  ejecutada_en: z.string(),
  matriz: z.array(z.object({
    criterio_id: z.string(),
    resultado: z.enum(["pasa", "falla", "flaky"] as const),
    extracto_fallo: z.string().nullable(),
    evidencias: z.array(z.object({ tipo: z.string(), adjunto_id: z.string().optional(), blob_ref: z.string().optional() })),
  })),
  regresion: z.object({ 
    tests_dirigidos: z.number(),
    dirigidos_ok: z.number(), 
    suite_cliente: z.number(), 
    cliente_ok: z.number() 
  }),
  flaky: z.array(z.object({
    test_id: z.string(),
    tasa_fallo: z.number(),
    ultimo_fallo: z.string()
  })),
});
export type Evidencia = z.infer<typeof EvidenciaSchema>;

export const EventoAuditoriaSchema = z.object({
  id: z.string(),
  fecha: z.string(),
  ticket_id: z.string().nullable(),
  etapa: z.string().nullable(),
  actor: z.object({
    tipo: z.string(),
    nombre: z.string(),
  }),
  verificado: z.boolean().optional(),
  evento: z.string(),
  modelo: z.string().nullable(),
  sello: z.string(),
});
export type EventoAuditoria = z.infer<typeof EventoAuditoriaSchema>;

export interface EventosAuditoria {
  items: EventoAuditoria[];
  total: number;
  cadena: { eventos_hoy: number };
}

export const ExpedienteSchema = z.object({
  solicitante: z.object({ nombre: z.string().nullable(), origen: z.string(), fecha: z.string() }),
  integridad: z.object({ integra: z.boolean(), hash_raiz: z.string().nullable() }),
  firmas: z.array(z.object({
    gate: z.number(),
    actor: z.string(),
    estado: z.string(),
    sello: z.string(),
  })),
  etapas: z.array(z.object({ etapa: z.string(), pasos: z.array(z.object({ step_id: z.string(), paso: z.string(), modelo: z.string(), tier: z.string(), confianza: z.number().nullable(), validacion: z.string(), tokens: z.number(), duracion_ms: z.number(), etapa: z.string() })) })),
  cambios: z.array(z.object({ archivo: z.string(), pr_url: z.string().nullable(), racional: z.string() })).optional(),
  eventos: z.array(z.object({ id: z.string(), creado_en: z.string(), actor_tipo: z.string(), actor_id: z.string().nullable(), resumen: z.string(), sello: z.string() })).optional(),
});
export type Expediente = z.infer<typeof ExpedienteSchema>;

export const AprobacionesPendientesSchema = z.object({
  items: z.array(z.object({
    ticket_id: z.string(),
    titulo: z.string(),
    gate: z.number(),
    que_se_aprueba: z.string(),
    esperando_desde: z.string(),
  }))
});
export type AprobacionesPendientes = z.infer<typeof AprobacionesPendientesSchema>;

export type EstadoGates = Record<Etapa, number | undefined>;

// --- TIPOS DE APOYO PARA LAS VISTAS Y MOCKS ---
export interface Usuario { id: string; nombre: string; email: string; rol: Rol; idioma: string; org_id: string; }
export interface PerfilPropio { id: string; nombre: string; email: string; rol: Rol; idioma: string; }
export interface Sesion { access_token: string; refresh_token: string; usuario: Usuario; acceso_restringido: boolean; }
export interface TicketResumen { id: string; titulo: string; tipo: TipoTicket; prioridad: Prioridad; etapa: Etapa; gate_pendiente: number | null; origen: string; actualizado_en: string; }
export interface Paginado<T> { items: T[]; total: number; page: number; page_size: number; }

export interface Spec { 
  version: number; 
  estado: "pendiente_gate1" | "aprobada" | "reemplazada" | "rechazada"; 
  alternativas: Array<{ id: string; titulo: string; descripcion: string; elegida?: boolean; recomendada?: boolean; riesgo?: string }>; 
  criterios: Array<{ id: string; given: string; when: string; then: string }>; 
  scope_paths: string[]; 
  estimacion_loc: number | null; 
}

export interface Hallazgos { 
  hallazgos: Array<{ id: string; severidad: string; titulo: string; justificacion?: string; estado: string }>; 
  bloquea_deploy: boolean; 
}

export interface ConsumoTicket { 
  totales: { tokens_in: number; tokens_out: number; loc_netas: number; loc_tests: number; invocaciones: number }; 
  por_funcion: Array<{ funcion: string; modelo: string; invocaciones: number; tokens_in: number; tokens_out: number; duracion_media_ms?: number }>; 
  desglose_loc: unknown; 
}

export interface Actividad { 
  items: Array<{ fecha: string; actor: { tipo: string; nombre: string }; evento: string; etapa?: string }>; 
}

export interface Lineas { 
  lineas: Array<{ id: string; nombre: string; estado: string; rama?: string; numero?: number; ticket_id?: string | null; etapa?: string | null }>; 
  en_espera: number; 
  contratadas: number | null; 
}

export interface UsageSummary { 
  ciclo: string; 
  plan: { codigo: string; nombre?: string; loc_mes?: number; renueva_el?: string; lineas?: number }; 
  loc: { loc_netas: number; loc_mes: number; consumidas: number; restantes: number; porcentaje: number }; 
  tickets: { total: number; cerrados?: number; en_proceso?: number }; 
  umbral: "normal" | "aviso_80" | "agotado"; 
}

export interface AsignacionesLLM { 
  politica_escalado: string; 
  asignaciones: Array<{ funcion: string; modelo: string; paso?: string; etapa?: string; tier: string; escalable?: boolean; descripcion?: string; version?: string }>; 
}

export interface Notificaciones { 
  items: Array<{ id: string; mensaje?: string; tipo?: string; titulo?: string; cuerpo?: string | null; ticket_id?: string | null; creada_en?: string; leida?: boolean }>; 
  no_leidas: number; 
}

export interface ResumenTablero { 
  en_cola: number; 
  en_proceso: number; 
  esperando_aprobacion: number; 
  en_produccion_30d: number; 
}

export interface Kanban { 
  columnas: Record<string, Array<{ id: string; titulo: string; prioridad: string; etapa: string; gate_pendiente?: number | null }>>; 
}

export interface PasoLLM { etapa: string; paso: string; modelo: string; tier: string; confianza: number | null; validacion: string; tokens: number; duracion_ms: number; step_id: string; }
export interface Conexion { id: string; categoria: string; proveedor: string; nombre: string | null; estado: string; ultimo_test: string | null; }
export interface Conexiones { conexiones: Conexion[]; mapa_ramas: Record<string, string> | null; }

export interface ProveedoresLLM { 
  modalidad: string; 
  tiers: Array<{ nombre: string; feature?: string; staging?: string; produccion?: string; tier: string; modelo?: string; perfil?: string }>; 
}

export interface ConsumoPorTicket { ticket_id: string; titulo: string; tipo: string; loc_netas: number; tokens: number; etapa: string; }
export interface Similar { ticket_id: string; titulo: string; similitud: number; etapa: string; resuelto_en: string | null; }
export interface Impacto { modulos: string[]; tablas: string[]; confianza: number; }
export interface CriterioGWT { given: string; when: string; then: string; }