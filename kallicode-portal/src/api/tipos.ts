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

// 1. OrganizacionSchema: Corrige fabrica_activa (PT-F09c)
export const OrganizacionSchema = z.object({
  id: z.string(),
  nombre: z.string(),
  idioma: z.string(),
  timezone: z.string(),
  despliegue: z.string(),
  fabrica_activa: z.boolean(), // Corregido: ya no es Organizacion.fabrica_activa
  plan: z.object({
    codigo: z.string(),
    nombre: z.string(),
    lineas: z.number().nullable(),
    loc_mes: z.number().nullable(),
    renueva_el: z.string(),
  }).nullable(),
  polling: z.object({ notificaciones_s: z.number(), tablero_s: z.number() }), // Corregido: ya no es Organizacion.polling
});
export type Organizacion = z.infer<typeof OrganizacionSchema>;

// 2. TicketDetalleSchema: Corrige radio_impacto (PT-F19)
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
    radio_impacto: z.array(z.string()), // Corregido (antes unknown)
    estimacion: z.string().nullable(),
    pr_url: z.string().nullable(),
  }),
  visualizadores: z.array(z.object({ id: z.string(), nombre: z.string() })),
});
export type TicketDetalle = z.infer<typeof TicketDetalleSchema>;

// 3. EvidenciaSchema: Corrige regresion y flaky (PT-F09a y PT-F19)
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
    tests_dirigidos: z.number(), // Corregido a partir de dirigidos (PT-F09a)
    dirigidos_ok: z.number(), 
    suite_cliente: z.number(), 
    cliente_ok: z.number() 
  }),
  flaky: z.array(z.object({
    test_id: z.string(),
    tasa_fallo: z.number(),
    ultimo_fallo: z.string()
  })), // Corregido (antes unknown[])
});
export type Evidencia = z.infer<typeof EvidenciaSchema>;

// 4. AuditoriaSchema: Corrige id, actor y actor_tipo (PT-F09b)
export const EventoAuditoriaSchema = z.object({
  id: z.string(), // Corregido (antes number)
  fecha: z.string(),
  ticket_id: z.string().nullable(),
  etapa: z.string().nullable(),
  actor: z.object({
    tipo: z.string(), // Corregido: agrupado dentro de actor.tipo en vez de actor_tipo
    nombre: z.string(),
  }),
  verificado: z.boolean().optional(), // Corregido
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

// 5. ExpedienteSchema: Corrige las firmas (PT-F09d)
export const ExpedienteSchema = z.object({
  solicitante: z.object({ nombre: z.string().nullable(), origen: z.string(), fecha: z.string() }),
  integridad: z.object({ integra: z.boolean(), hash_raiz: z.string().nullable() }),
  firmas: z.array(z.object({
    gate: z.number(),
    actor: z.string(),
    estado: z.string(), // Corregido (antes accion)
    sello: z.string(),
  })), // Eliminado fecha y comentario que no estaban garantizados por el contrato §11.2
  etapas: z.array(z.object({ etapa: z.string(), pasos: z.any() })),
  cambios: z.array(z.object({ archivo: z.string(), pr_url: z.string().nullable(), racional: z.string() })).optional(),
  eventos: z.array(z.any()).optional(),
});
export type Expediente = z.infer<typeof ExpedienteSchema>;

// 6. AprobacionesPendientes y EstadoGates (PT-F19)
export const AprobacionesPendientesSchema = z.object({ // Renombrado de Aprobaciones
  items: z.array(z.object({
    ticket_id: z.string(),
    titulo: z.string(),
    gate: z.number(),
    que_se_aprueba: z.string(),
    esperando_desde: z.string(),
  }))
});
export type AprobacionesPendientes = z.infer<typeof AprobacionesPendientesSchema>;

export type EstadoGates = Record<Etapa, number | undefined>; // Corregido (PT-F19)

// --- TIPOS NO AFECTADOS (Migrados tal cual estaban) ---
export interface Usuario { id: string; nombre: string; email: string; rol: Rol; idioma: string; org_id: string; }
export interface PerfilPropio { id: string; nombre: string; email: string; rol: Rol; idioma: string; }
export interface Sesion { access_token: string; refresh_token: string; usuario: Usuario; acceso_restringido: boolean; }
export interface TicketResumen { id: string; titulo: string; tipo: TipoTicket; prioridad: Prioridad; etapa: Etapa; gate_pendiente: number | null; origen: string; actualizado_en: string; }
export interface Paginado<T> { items: T[]; total: number; page: number; page_size: number; }
export interface Spec { version: number; estado: "pendiente_gate1" | "aprobada" | "reemplazada" | "rechazada"; alternativas: any[]; criterios: any[]; scope_paths: string[]; estimacion_loc: number | null; }
export interface Hallazgos { hallazgos: any[]; bloquea_deploy: boolean; }
export interface ConsumoTicket { totales: any; por_funcion: any[]; desglose_loc: any; }
export interface Actividad { items: any[]; }
export interface Lineas { lineas: any[]; en_espera: number; contratadas: number | null; }
export interface UsageSummary { ciclo: string; plan: any; loc: any; tickets: any; umbral: "normal" | "aviso_80" | "agotado"; }
export interface AsignacionesLLM { politica_escalado: string; asignaciones: any[]; }
export interface Notificaciones { items: any[]; no_leidas: number; }
export interface ResumenTablero { en_cola: number; en_proceso: number; esperando_aprobacion: number; en_produccion_30d: number; }
export interface Kanban { columnas: Record<string, any[]>; }
export interface PasoLLM { etapa: string; paso: string; modelo: string; tier: string; confianza: number | null; validacion: string; tokens: number; duracion_ms: number; step_id: string; }
export interface Conexion { id: string; categoria: string; proveedor: string; nombre: string | null; estado: string; ultimo_test: string | null; }
export interface Conexiones { conexiones: Conexion[]; mapa_ramas: any | null; }
export interface ProveedoresLLM { modalidad: string; tiers: any[]; }
export interface ConsumoPorTicket { ticket_id: string; titulo: string; tipo: string; loc_netas: number; tokens: number; etapa: string; }
export interface Similar { ticket_id: string; titulo: string; similitud: number; etapa: string; resuelto_en: string | null; }
export interface Impacto { modulos: string[]; tablas: string[]; confianza: number; }
export interface CriterioGWT { given: string; when: string; then: string; }