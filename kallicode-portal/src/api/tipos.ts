/** Tipos de los contratos del backend (subset consumido por el portal). */

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
export type TipoTicket = "bug" | "mejora" | "funcionalidad" | "seguridad";
export type Rol = "owner" | "admin" | "architect" | "approver" | "member" | "viewer";

export interface Usuario {
  id: string;
  nombre: string;
  email: string;
  rol: Rol;
  idioma: string;
  org_id: string;
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
  estado: string;
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
  regresion: Record<string, number>;
  flaky: unknown[];
}

export interface Hallazgos {
  hallazgos: {
    id: string;
    severidad: string;
    titulo: string;
    origen: string;
    estado: string;
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
  lineas: { numero: number; estado: string; ticket_id: string | null; etapa: string | null }[];
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
    tipo: string;
    titulo: string;
    cuerpo: string | null;
    ticket_id: string | null;
    creada_en: string;
    leida: boolean;
  }[];
  no_leidas: number;
}
