/**
 * Contratos del backend que consume el portal (KC-PT-04-2, cierra PT-F09 y PT-F19).
 *
 * Cada respuesta tiene un esquema Zod y su tipo se deriva de él (z.infer): hay una sola
 * definición, así que el tipo y la validación no pueden desalinearse. El cliente valida
 * cada respuesta con el esquema que le pasa quien llama (api/consultas.ts).
 *
 * - Solo se declaran los campos que usa el portal; los demás se descartan al validar.
 *   Si una vista necesita un campo nuevo, se agrega aquí y TypeScript obliga a validarlo.
 * - Los cuerpos de entrada salen del contrato congelado (api/generado/api-v1.ts). Las
 *   respuestas se escriben aquí porque el contrato aún las publica como `object` sin
 *   campos: el backend no declara `response_model` en /api/v1.
 * - Los esquemas se contrastaron con las respuestas reales de la API; spec, evidence y
 *   security, con el diseño v1.1 (hoy el backend responde 500 en esas rutas).
 *
 * Inventario campo a campo y divergencias conocidas: docs/inventario-contrato-v1.md.
 */
import { z } from "zod";

import type { components } from "./generado/api-v1";

/* ---------------- valores cerrados --------------------------------------- */

export const ETAPAS = [
  "triage",
  "design",
  "build",
  "qa",
  "security",
  "deploy",
  "produccion",
  "en_cola_por_cuota",
  "cancelado",
  "cerrado_duplicado",
] as const;
export const EtapaEsquema = z.enum(ETAPAS);
export type Etapa = z.infer<typeof EtapaEsquema>;

export const PrioridadEsquema = z.enum(["alta", "media", "baja"]);
export type Prioridad = z.infer<typeof PrioridadEsquema>;

export const TipoTicketEsquema = z.enum(["bug", "mejora", "funcionalidad", "seguridad"]);
export type TipoTicket = z.infer<typeof TipoTicketEsquema>;

/** Tope de `texto` en dedup-preview e impact-preview (PreviewIn.texto, max_length). */
export const MAX_TEXTO_PREVIEW = 2000;

/** core.users.rol */
export const ROLES = ["owner", "admin", "architect", "approver", "member", "viewer"] as const;
export const RolEsquema = z.enum(ROLES);
export type Rol = z.infer<typeof RolEsquema>;
export const esRol = (valor: unknown): valor is Rol => RolEsquema.safeParse(valor).success;

/** core.jobs.estado: lo que muestra cada línea del tablero (no es la etapa del ticket). */
export const EstadoJobEsquema = z.enum([
  "pendiente_asignacion",
  "triage",
  "design",
  "gate1",
  "build",
  "qa",
  "gate2",
  "security",
  "deploy_preflight",
  "gate3",
  "deploy",
  "produccion",
  "pausado_por_cuota",
  "escalado_humano",
  "cancelado",
  "cerrado_duplicado",
]);
export type EstadoJob = z.infer<typeof EstadoJobEsquema>;

/* ---------------- sesión y organización ---------------------------------- */

export const UsuarioEsquema = z.object({
  id: z.string(),
  nombre: z.string(),
  email: z.string(),
  rol: RolEsquema,
  idioma: z.string(),
  org_id: z.string(),
});
export type Usuario = z.infer<typeof UsuarioEsquema>;

/** GET /users/me: perfil vigente en el servidor (subset consumido). No trae org_id. */
export const PerfilPropioEsquema = UsuarioEsquema.omit({ org_id: true });
export type PerfilPropio = z.infer<typeof PerfilPropioEsquema>;

/** POST /auth/login (TokensOut). */
export const SesionEsquema = z.object({
  access_token: z.string(),
  refresh_token: z.string(),
  usuario: UsuarioEsquema,
  acceso_restringido: z.boolean(),
});
export type Sesion = z.infer<typeof SesionEsquema>;

const PlanEsquema = z.object({
  codigo: z.string(),
  nombre: z.string(),
  lineas: z.number().nullable(),
  loc_mes: z.number().nullable(),
  renueva_el: z.string(),
});

/**
 * GET /organization. `polling` admite cualquier valor: useIntervalosPolling descarta los
 * inválidos y usa los de D12, en vez de dejar la pantalla en error (KC-PT-04-4).
 */
export const OrganizacionEsquema = z.object({
  id: z.string(),
  nombre: z.string(),
  idioma: z.string(),
  timezone: z.string(),
  despliegue: z.string(),
  fabrica_activa: z.boolean(),
  plan: PlanEsquema.nullable(),
  polling: z.object({ notificaciones_s: z.unknown(), tablero_s: z.unknown() }).nullish(),
});
export type Organizacion = z.infer<typeof OrganizacionEsquema>;

/* ---------------- listados ----------------------------------------------- */

export interface Paginado<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** Listado paginado con `total` (§3.4): tickets, auditoría, consumo por ticket. */
export const paginado = <T extends z.ZodType>(item: T) =>
  z.object({
    items: z.array(item),
    total: z.number(),
    page: z.number(),
    page_size: z.number(),
  });

/* ---------------- tickets ------------------------------------------------ */

export const TicketResumenEsquema = z.object({
  id: z.string(),
  titulo: z.string(),
  tipo: TipoTicketEsquema,
  prioridad: PrioridadEsquema,
  etapa: EtapaEsquema,
  gate_pendiente: z.number().nullable(),
  origen: z.string(),
  actualizado_en: z.string(),
});
export type TicketResumen = z.infer<typeof TicketResumenEsquema>;

export const TicketDetalleEsquema = z.object({
  id: z.string(),
  titulo: z.string(),
  tipo: TipoTicketEsquema,
  prioridad: PrioridadEsquema,
  origen: z.string(),
  reportado_por: z.object({ id: z.string(), nombre: z.string() }).nullable(),
  etapa: EtapaEsquema,
  gate_pendiente: z.number().nullable(),
  pipeline: z.array(
    z.object({
      etapa: z.string(),
      estado: z.enum(["done", "actual", "pendiente"]),
      gate: z.number().nullable(),
    }),
  ),
  detalle: z.object({
    linea: z.number().nullable(),
    radio_impacto: z.unknown(),
    estimacion: z.string().nullable(),
    pr_url: z.string().nullable(),
  }),
  visualizadores: z.array(z.object({ id: z.string(), nombre: z.string() })),
});
export type TicketDetalle = z.infer<typeof TicketDetalleEsquema>;

export const SpecEsquema = z.object({
  version: z.number(),
  estado: z.enum(["pendiente_gate1", "aprobada", "reemplazada", "rechazada"]),
  alternativas: z.array(
    z.object({
      id: z.string(),
      titulo: z.string(),
      descripcion: z.string(),
      riesgo: z.string(),
      recomendada: z.boolean(),
      elegida: z.boolean(),
    }),
  ),
  criterios: z.array(
    z.object({ id: z.string(), given: z.string(), when: z.string(), then: z.string() }),
  ),
  scope_paths: z.array(z.string()),
  estimacion_loc: z.number().nullable(),
});
export type Spec = z.infer<typeof SpecEsquema>;

export const EvidenciaEsquema = z.object({
  corrida: z.number(),
  ejecutada_en: z.string(),
  matriz: z.array(
    z.object({
      criterio_id: z.string(),
      resultado: z.enum(["pasa", "falla", "flaky"]),
      extracto_fallo: z.string().nullable(),
      evidencias: z.array(
        z.object({
          tipo: z.string(),
          adjunto_id: z.string().optional(),
          blob_ref: z.string().optional(),
        }),
      ),
    }),
  ),
  regresion: z.object({
    dirigidos: z.number(),
    dirigidos_ok: z.number(),
    suite_cliente: z.number(),
    cliente_ok: z.number(),
  }),
  flaky: z.array(z.unknown()),
});
export type Evidencia = z.infer<typeof EvidenciaEsquema>;

export const HallazgosEsquema = z.object({
  hallazgos: z.array(
    z.object({
      id: z.string(),
      severidad: z.enum(["critica", "alta", "media", "baja"]),
      titulo: z.string(),
      origen: z.enum(["sast", "sca", "secrets"]),
      estado: z.enum(["abierto", "corregido", "descartado"]),
      justificacion: z.string().nullable(),
    }),
  ),
  bloquea_deploy: z.boolean(),
});
export type Hallazgos = z.infer<typeof HallazgosEsquema>;

export const ConsumoTicketEsquema = z.object({
  totales: z.object({
    tokens_in: z.number(),
    tokens_out: z.number(),
    invocaciones: z.number(),
    loc_netas: z.number(),
    loc_tests: z.number(),
  }),
  por_funcion: z.array(
    z.object({
      funcion: z.string(),
      modelo: z.string(),
      invocaciones: z.number(),
      tokens_in: z.number(),
      tokens_out: z.number(),
      duracion_media_ms: z.number(),
    }),
  ),
  desglose_loc: z.object({
    anadidas: z.number(),
    eliminadas: z.number(),
    netas: z.number(),
    tests: z.number(),
  }),
});
export type ConsumoTicket = z.infer<typeof ConsumoTicketEsquema>;

/** GET /tickets/{numero}/activity: paginado sin `total`. */
export const ActividadEsquema = z.object({
  items: z.array(
    z.object({
      fecha: z.string(),
      actor: z.object({ tipo: z.string(), nombre: z.string() }),
      evento: z.string(),
      etapa: z.string().nullable(),
    }),
  ),
  page: z.number(),
  page_size: z.number(),
});
export type Actividad = z.infer<typeof ActividadEsquema>;

/** Similitud: Postgres la calcula como numeric y puede llegar como texto. */
export const SimilaresEsquema = z.object({
  similares: z.array(
    z.object({
      ticket_id: z.string(),
      titulo: z.string(),
      similitud: z.coerce.number(),
      etapa: z.string(),
      resuelto_en: z.string().nullable(),
    }),
  ),
});
export type Similar = z.infer<typeof SimilaresEsquema>["similares"][number];

export const ImpactoEsquema = z.object({
  modulos: z.array(z.string()),
  tablas: z.array(z.string()),
  confianza: z.number(),
});
export type Impacto = z.infer<typeof ImpactoEsquema>;

/** Respuesta de crear ticket o funcionalidad: la vista navega al ticket creado. */
export const TicketCreadoEsquema = z.object({ id: z.string(), etapa: EtapaEsquema });

/**
 * POST /tickets/attachments: el contrato no tipa la respuesta (dict). El navegador sube
 * el archivo a `upload_url`, así que solo se acepta http(s).
 */
export const AdjuntoPresignEsquema = z.object({
  adjunto_id: z.string(),
  upload_url: z.url({ protocol: /^https?$/ }),
  expira_en: z.string(),
});
export type AdjuntoPresign = z.infer<typeof AdjuntoPresignEsquema>;

/* Respuestas de otras mutaciones: solo el campo que muestra la vista. */
export const ResultadoTestEsquema = z.object({ estado: z.string() });
export const FabricaEncendidaEsquema = z.object({ lineas_activadas: z.number() });
export const UpgradeSolicitadoEsquema = z.object({ mensaje: z.string() });

/* ---------------- tablero y aprobaciones --------------------------------- */

export const ResumenTableroEsquema = z.object({
  en_cola: z.number(),
  en_proceso: z.number(),
  esperando_aprobacion: z.number(),
  en_produccion_30d: z.number(),
});
export type ResumenTablero = z.infer<typeof ResumenTableroEsquema>;

export const LineasEsquema = z.object({
  lineas: z.array(
    z.object({
      numero: z.number(),
      estado: z.enum(["disponible", "ocupada", "mantenimiento"]),
      ticket_id: z.string().nullable(),
      /** Estado del job en la línea, no la etapa del ticket. */
      etapa: EstadoJobEsquema.nullable(),
    }),
  ),
  en_espera: z.number(),
  contratadas: z.number().nullable(),
});
export type Lineas = z.infer<typeof LineasEsquema>;

export const KanbanEsquema = z.object({
  columnas: z.record(
    z.string(),
    z.array(
      z.object({
        id: z.string(),
        titulo: z.string(),
        tipo: z.string(),
        prioridad: z.string(),
        gate_pendiente: z.number().nullable(),
      }),
    ),
  ),
});
export type Kanban = z.infer<typeof KanbanEsquema>;

export const AprobacionesEsquema = z.object({
  items: z.array(
    z.object({
      ticket_id: z.string(),
      titulo: z.string(),
      gate: z.number(),
      que_se_aprueba: z.string(),
      esperando_desde: z.string(),
    }),
  ),
});
export type Aprobaciones = z.infer<typeof AprobacionesEsquema>;

/** GET /notifications: el badge solo usa `no_leidas`. */
export const NotificacionesEsquema = z.object({ no_leidas: z.number() });

/* ---------------- auditoría ---------------------------------------------- */

export const EventoAuditoriaEsquema = z.object({
  id: z.number(),
  fecha: z.string(),
  ticket_id: z.string().nullable(),
  etapa: z.string().nullable(),
  actor_tipo: z.string(),
  actor_id: z.string().nullable(),
  evento: z.string(),
  modelo: z.string().nullable(),
  sello: z.string(),
});
export type EventoAuditoria = z.infer<typeof EventoAuditoriaEsquema>;

export const EventosAuditoriaEsquema = paginado(EventoAuditoriaEsquema).extend({
  cadena: z.object({ eventos_hoy: z.number() }),
});
export type EventosAuditoria = z.infer<typeof EventosAuditoriaEsquema>;

export const PasoLLMEsquema = z.object({
  etapa: z.string(),
  paso: z.string(),
  modelo: z.string(),
  tier: z.string(),
  confianza: z.number().nullable(),
  validacion: z.string(),
  tokens: z.number(),
  duracion_ms: z.number(),
  step_id: z.string(),
});
export type PasoLLM = z.infer<typeof PasoLLMEsquema>;

export const ExpedienteEsquema = z.object({
  solicitante: z.object({ nombre: z.string().nullable(), origen: z.string(), fecha: z.string() }),
  integridad: z.object({ integra: z.boolean(), hash_raiz: z.string().nullable() }),
  firmas: z.array(
    z.object({
      gate: z.number(),
      accion: z.string(),
      actor: z.string(),
      fecha: z.string(),
      comentario: z.string().nullable(),
      sello: z.string(),
    }),
  ),
  etapas: z.array(z.object({ etapa: z.string(), pasos: z.array(PasoLLMEsquema) })),
  /** §11.2: cambios de código con racional por archivo. El backend los entrega en KC-BE-08-6. */
  cambios: z
    .array(z.object({ archivo: z.string(), pr_url: z.string().nullable(), racional: z.string() }))
    .optional(),
  /**
   * Transitorio: el backend actual envía `eventos` en vez de `cambios`. Se sigue mostrando
   * para no perder información; se retira cuando KC-BE-08-6 entregue `cambios`.
   */
  eventos: z
    .array(
      z.object({
        id: z.number(),
        creado_en: z.string(),
        actor_tipo: z.string(),
        actor_id: z.string().nullable(),
        resumen: z.string(),
        sello: z.string(),
      }),
    )
    .optional(),
});
export type Expediente = z.infer<typeof ExpedienteEsquema>;

/* ---------------- conexiones, modelos y consumo -------------------------- */

export const ConexionesEsquema = z.object({
  conexiones: z.array(
    z.object({
      id: z.string(),
      categoria: z.string(),
      proveedor: z.string(),
      nombre: z.string().nullable(),
      estado: z.string(),
      ultimo_test: z.string().nullable(),
    }),
  ),
  mapa_ramas: z
    .object({ feature: z.string(), staging: z.string(), produccion: z.string() })
    .nullable(),
});
export type Conexiones = z.infer<typeof ConexionesEsquema>;
export type Conexion = Conexiones["conexiones"][number];

export const ProveedoresLLMEsquema = z.object({
  modalidad: z.string(),
  tiers: z.array(
    z.object({ tier: z.string(), modelo: z.string(), nombre: z.string(), perfil: z.string() }),
  ),
});
export type ProveedoresLLM = z.infer<typeof ProveedoresLLMEsquema>;

export const AsignacionesLLMEsquema = z.object({
  politica_escalado: z.string(),
  asignaciones: z.array(
    z.object({
      paso: z.string(),
      etapa: z.string(),
      tier: z.string(),
      modelo: z.string(),
      escalable: z.boolean(),
      descripcion: z.string(),
      version: z.string(),
    }),
  ),
});
export type AsignacionesLLM = z.infer<typeof AsignacionesLLMEsquema>;

export const UsageSummaryEsquema = z.object({
  ciclo: z.string(),
  plan: PlanEsquema.omit({ nombre: true }).nullable(),
  loc: z.object({
    consumidas: z.number(),
    restantes: z.number().nullable(),
    porcentaje: z.number(),
  }),
  tickets: z.object({ cerrados: z.number(), en_proceso: z.number() }),
  umbral: z.enum(["normal", "aviso_80", "agotado"]),
});
export type UsageSummary = z.infer<typeof UsageSummaryEsquema>;

export const ConsumoPorTicketEsquema = z.object({
  ticket_id: z.string(),
  titulo: z.string(),
  tipo: z.string(),
  loc_netas: z.number(),
  tokens: z.number(),
  etapa: z.string(),
});
export type ConsumoPorTicket = z.infer<typeof ConsumoPorTicketEsquema>;

/* ---------------- cuerpos de entrada (generados del contrato) ------------ */

type Entrada = components["schemas"];

/** POST /tickets (TicketIn), con los valores que ofrece el formulario. */
export type NuevoTicketIn = Entrada["TicketIn"] & {
  tipo: "bug" | "mejora";
  prioridad: Prioridad;
};
/** POST /tickets/features (FeatureIn). */
export type NuevaFuncionalidadIn = Entrada["FeatureIn"];
export type CriterioGWT = Entrada["CriterioIn"];
/** POST /tickets/{numero}/gates/{gate}/approve (FirmaIn). */
export type FirmaGateIn = Entrada["FirmaIn"];
/** POST /tickets/{numero}/gates/{gate}/request-changes (CambiosIn). */
export type CambiosGateIn = Entrada["CambiosIn"];
/** PATCH /tickets/{numero} (TicketPatch). */
export type TicketPatchIn = Entrada["TicketPatch"];
/** POST /tickets/dedup-preview e impact-preview (PreviewIn). */
export type PreviewIn = Entrada["PreviewIn"];
/** POST /tickets/attachments (AdjuntoIn). */
export type AdjuntoIn = Entrada["AdjuntoIn"];
/** POST /plans/upgrade-request (UpgradeIn). */
export type UpgradeIn = Entrada["UpgradeIn"];
/** POST /auth/login (LoginIn). */
export type LoginIn = Entrada["LoginIn"];
