/**
 * Tipos de los contratos del backend (subset consumido por el portal).
 * Inventario campo a campo y divergencias conocidas: docs/inventario-contrato-v1.md.
 */

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

/** Tope de `texto` en dedup-preview e impact-preview (PreviewIn.texto, max_length). */
export const MAX_TEXTO_PREVIEW = 2000;
export type TipoTicket = "bug" | "mejora" | "funcionalidad" | "seguridad";
/** core.users.rol */
export const ROLES = ["owner", "admin", "architect", "approver", "member", "viewer"] as const;
export type Rol = (typeof ROLES)[number];
export const esRol = (valor: unknown): valor is Rol => ROLES.some((r) => r === valor);
/** core.jobs.estado: lo que muestra cada línea del tablero (no es la etapa del ticket). */
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

export interface Usuario {
  id: string;
  nombre: string;
  email: string;
  rol: Rol;
  idioma: string;
  org_id: string;
}

/** GET /users/me: perfil vigente en el servidor (subset consumido). No trae org_id. */
export interface PerfilPropio {
  id: string;
  nombre: string;
  email: string;
  rol: Rol;
  idioma: string;
}

export interface Sesion {
  access_token: string;
  refresh_token: string;
  usuario: Usuario;
  acceso_restringido: boolean;
}

export interface Organizacion {
  id: string;
  nombre: string;
  idioma: string;
  timezone: string;
  despliegue: string;
  fabrica_activa: boolean;
  plan: {
    codigo: string;
    nombre: string;
    lineas: number | null;
    loc_mes: number | null;
    renueva_el: string;
  } | null;
  polling: { notificaciones_s: number; tablero_s: number };
}

export interface TicketResumen {
  id: string;
  titulo: string;
  tipo: TipoTicket;
  prioridad: Prioridad;
  etapa: Etapa;
  gate_pendiente: number | null;
  origen: string;
  actualizado_en: string;
}

export interface Paginado<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface TicketDetalle {
  id: string;
  titulo: string;
  tipo: TipoTicket;
  prioridad: Prioridad;
  origen: string;
  reportado_por: { id: string; nombre: string } | null;
  etapa: Etapa;
  gate_pendiente: number | null;
  pipeline: { etapa: string; estado: "done" | "actual" | "pendiente"; gate: number | null }[];
  detalle: {
    linea: number | null;
    radio_impacto: unknown;
    estimacion: string | null;
    pr_url: string | null;
  };
  visualizadores: { id: string; nombre: string }[];
}

export interface Spec {
  version: number;
  estado: "pendiente_gate1" | "aprobada" | "reemplazada" | "rechazada";
  alternativas: {
    id: string;
    titulo: string;
    descripcion: string;
    riesgo: string;
    recomendada: boolean;
    elegida: boolean;
  }[];
  criterios: { id: string; given: string; when: string; then: string }[];
  scope_paths: string[];
  estimacion_loc: number | null;
}

export interface Evidencia {
  corrida: number;
  ejecutada_en: string;
  matriz: {
    criterio_id: string;
    resultado: "pasa" | "falla" | "flaky";
    extracto_fallo: string | null;
    evidencias: { tipo: string; adjunto_id?: string; blob_ref?: string }[];
  }[];
  regresion: { dirigidos: number; dirigidos_ok: number; suite_cliente: number; cliente_ok: number };
  flaky: unknown[];
}

export interface Hallazgos {
  hallazgos: {
    id: string;
    severidad: "critica" | "alta" | "media" | "baja";
    titulo: string;
    origen: "sast" | "sca" | "secrets";
    estado: "abierto" | "corregido" | "descartado";
    justificacion: string | null;
  }[];
  bloquea_deploy: boolean;
}

export interface ConsumoTicket {
  totales: {
    tokens_in: number;
    tokens_out: number;
    invocaciones: number;
    loc_netas: number;
    loc_tests: number;
  };
  por_funcion: {
    funcion: string;
    modelo: string;
    invocaciones: number;
    tokens_in: number;
    tokens_out: number;
    duracion_media_ms: number;
  }[];
  desglose_loc: { anadidas: number; eliminadas: number; netas: number; tests: number };
}

export interface Actividad {
  items: {
    fecha: string;
    actor: { tipo: string; nombre: string };
    evento: string;
    etapa: string | null;
  }[];
}

export interface EstadoGates {
  gates: {
    gate: number;
    estado: string;
    actor?: string;
    fecha?: string;
    comentario?: string | null;
    sello?: string;
  }[];
}

export interface Aprobaciones {
  items: {
    ticket_id: string;
    titulo: string;
    gate: number;
    que_se_aprueba: string;
    esperando_desde: string;
  }[];
}

export interface Lineas {
  lineas: {
    numero: number;
    estado: "disponible" | "ocupada" | "mantenimiento";
    ticket_id: string | null;
    /** Estado del job en la línea, no la etapa del ticket. */
    etapa: EstadoJob | null;
  }[];
  en_espera: number;
  contratadas: number | null;
}

export interface UsageSummary {
  ciclo: string;
  plan: {
    codigo: string;
    loc_mes: number | null;
    lineas: number | null;
    renueva_el: string;
  } | null;
  loc: { consumidas: number; restantes: number | null; porcentaje: number };
  tickets: { cerrados: number; en_proceso: number };
  umbral: "normal" | "aviso_80" | "agotado";
}

export interface AsignacionesLLM {
  politica_escalado: string;
  asignaciones: {
    paso: string;
    etapa: string;
    tier: string;
    modelo: string;
    escalable: boolean;
    descripcion: string;
    version: string;
  }[];
}

export interface Notificaciones {
  items: {
    id: string;
    tipo:
      | "cambio_etapa"
      | "gate_pendiente"
      | "ticket_cerrado"
      | "aviso_cuota"
      | "seguridad"
      | "sistema";
    titulo: string;
    cuerpo: string | null;
    ticket_id: string | null;
    creada_en: string;
    leida: boolean;
  }[];
  no_leidas: number;
}

/* Tipos que vivían en cada vista: los hooks de api/consultas.ts los comparten (KC-PT-04-1). */

export interface ResumenTablero {
  en_cola: number;
  en_proceso: number;
  esperando_aprobacion: number;
  en_produccion_30d: number;
}

export interface Kanban {
  columnas: Record<
    string,
    { id: string; titulo: string; tipo: string; prioridad: string; gate_pendiente: number | null }[]
  >;
}

export interface EventoAuditoria {
  id: number;
  fecha: string;
  ticket_id: string | null;
  etapa: string | null;
  actor_tipo: string;
  actor_id: string | null;
  evento: string;
  modelo: string | null;
  sello: string;
}

export interface EventosAuditoria {
  items: EventoAuditoria[];
  total: number;
  cadena: { eventos_hoy: number };
}

export interface PasoLLM {
  etapa: string;
  paso: string;
  modelo: string;
  tier: string;
  confianza: number | null;
  validacion: string;
  tokens: number;
  duracion_ms: number;
  step_id: string;
}

export interface Expediente {
  solicitante: { nombre: string | null; origen: string; fecha: string };
  integridad: { integra: boolean; hash_raiz: string | null };
  firmas: {
    gate: number;
    accion: string;
    actor: string;
    fecha: string;
    comentario: string | null;
    sello: string;
  }[];
  etapas: { etapa: string; pasos: PasoLLM[] }[];
  /** §11.2: cambios de código con racional por archivo. El backend los entrega en KC-BE-08-6. */
  cambios?: { archivo: string; pr_url: string | null; racional: string }[];
  /**
   * Transitorio: el backend actual envía `eventos` en vez de `cambios`. Se sigue mostrando
   * para no perder información; se retira cuando KC-BE-08-6 entregue `cambios`.
   */
  eventos?: {
    id: number;
    creado_en: string;
    actor_tipo: string;
    actor_id: string | null;
    resumen: string;
    sello: string;
  }[];
}

export interface Conexion {
  id: string;
  categoria: string;
  proveedor: string;
  nombre: string | null;
  estado: string;
  ultimo_test: string | null;
}

export interface Conexiones {
  conexiones: Conexion[];
  mapa_ramas: { feature: string; staging: string; produccion: string } | null;
}

export interface ProveedoresLLM {
  modalidad: string;
  tiers: { tier: string; modelo: string; nombre: string; perfil: string }[];
}

export interface ConsumoPorTicket {
  ticket_id: string;
  titulo: string;
  tipo: string;
  loc_netas: number;
  tokens: number;
  etapa: string;
}

export interface Similar {
  ticket_id: string;
  titulo: string;
  similitud: number;
  etapa: string;
  resuelto_en: string | null;
}

export interface Impacto {
  modulos: string[];
  tablas: string[];
  confianza: number;
}

export interface CriterioGWT {
  given: string;
  when: string;
  then: string;
}
