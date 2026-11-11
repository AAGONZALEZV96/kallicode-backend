/**
 * Datos de prueba de los dobles MSW, calcados de db/seeds/dev.sql del backend
 * (tenant org_demo, tickets KC-1001..KC-1003). Tipados con api/tipos.ts donde el
 * contrato ya está declarado; el resto replica las interfaces locales de cada vista.
 */
import type {
  CadenaFirmas,
  Actividad,
  Aprobaciones,
  AsignacionesLLM,
  Bandeja,
  ConsumoTicket,
  Evidencia,
  Hallazgos,
  Lineas,
  Organizacion,
  Paginado,
  Sesion,
  Spec,
  TicketDetalle,
  TicketResumen,
  UsageSummary,
  Usuario,
} from "../api/tipos";

const FECHA = "2026-09-25T12:00:00Z";

export const usuarioOwner: Usuario = {
  id: "u_owner",
  nombre: "Olga Owner",
  email: "owner@demo.kallicode.dev",
  rol: "owner",
  idioma: "es",
  org_id: "org_demo",
};

export const PASSWORD_DEMO = "Dev12345!demo";

/** GET /users/me, como `_perfil()` del backend (sin org_id). */
export const perfilOwner = {
  id: usuarioOwner.id,
  nombre: usuarioOwner.nombre,
  email: usuarioOwner.email,
  rol: usuarioOwner.rol,
  idioma: usuarioOwner.idioma,
  estado: "activo",
  notificaciones: {},
  puede_firmar: [1, 2],
  ultimo_acceso: FECHA,
};

export const sesion: Sesion = {
  access_token: "acc_1",
  refresh_token: "ref_1",
  usuario: usuarioOwner,
  acceso_restringido: false,
};

// satisfies (no anotación): conserva `polling` como números para las pruebas de KC-PT-04-4.
export const organizacion = {
  id: "org_demo",
  nombre: "Demo Mobility",
  idioma: "es",
  timezone: "Europe/Madrid",
  despliegue: "saas",
  fabrica_activa: true,
  plan: { codigo: "growth", nombre: "Growth", lineas: 3, loc_mes: 20000, renueva_el: "2026-10-01" },
  polling: { notificaciones_s: 30, tablero_s: 60 },
} satisfies Organizacion;

export const tickets: TicketResumen[] = [
  {
    id: "KC-1001",
    titulo: "Timeout en búsqueda de clientes",
    tipo: "bug",
    prioridad: "alta",
    etapa: "triage",
    gate_pendiente: null,
    origen: "portal",
    actualizado_en: FECHA,
  },
  {
    id: "KC-1002",
    titulo: "Exportar CSV desde listados",
    tipo: "mejora",
    prioridad: "media",
    etapa: "triage",
    gate_pendiente: null,
    origen: "portal",
    actualizado_en: FECHA,
  },
  {
    id: "KC-1003",
    titulo: "Facturación por convenio corporativo",
    tipo: "funcionalidad",
    prioridad: "media",
    etapa: "design",
    gate_pendiente: 1,
    origen: "portal",
    actualizado_en: FECHA,
  },
];

export function paginar<T>(items: T[]): Paginado<T> {
  return { items, total: items.length, page: 1, page_size: 25 };
}

export function detalleTicket(id: string): TicketDetalle | null {
  const t = tickets.find((x) => x.id === id);
  if (!t) return null;
  return {
    ...t,
    reportado_por: { id: "u_dev", nombre: "María G." },
    pipeline: [
      { etapa: "triage", estado: "done", gate: null },
      { etapa: "design", estado: t.etapa === "design" ? "actual" : "pendiente", gate: 1 },
    ],
    // El backend aún envía estimacion siempre null (KC-PT-06-3).
    detalle: { linea: 1, radio_impacto: null, estimacion: null, pr_url: null },
    visualizadores: [],
  };
}

/** GET /tickets/{n}/gates: como gates.estado_gates, sin firmas en los tickets de ejemplo. */
export function cadenaFirmas(id: string): CadenaFirmas | null {
  const t = tickets.find((x) => x.id === id);
  if (!t) return null;
  return {
    gates: [1, 2, 3].map((gate) => ({
      gate,
      estado: t.gate_pendiente === gate ? ("pendiente" as const) : ("no_alcanzado" as const),
    })),
  };
}

export const resumenTablero = {
  en_cola: 2,
  en_proceso: 1,
  esperando_aprobacion: 1,
  en_produccion_30d: 0,
};

export const lineas: Lineas = {
  lineas: [
    // etapa es el estado del job: KC-1003 está en diseño esperando la firma del Gate 1.
    { numero: 1, estado: "ocupada", ticket_id: "KC-1003", etapa: "gate1" },
    { numero: 2, estado: "disponible", ticket_id: null, etapa: null },
  ],
  en_espera: 0,
  contratadas: 3,
};

/** Tarjeta del kanban: el backend solo envía estos cinco campos. */
function tarjeta({ id, titulo, tipo, prioridad, gate_pendiente }: TicketResumen) {
  return { id, titulo, tipo, prioridad, gate_pendiente };
}

export const kanban = {
  columnas: {
    triage: tickets.filter((t) => t.etapa === "triage").map(tarjeta),
    design: tickets.filter((t) => t.etapa === "design").map(tarjeta),
  },
};

export const aprobaciones: Aprobaciones = {
  items: [
    {
      ticket_id: "KC-1003",
      titulo: "Facturación por convenio corporativo",
      gate: 1,
      que_se_aprueba: "Diseño y alternativa",
      esperando_desde: FECHA,
    },
  ],
};

/**
 * GET /notifications. ticket_id es el id interno (tk_…), como guarda el backend;
 * ticket_numero es el campo que pide el portal: nt_2 no lo trae y se muestra sin enlace.
 */
export const notificaciones = {
  items: [
    {
      id: "nt_1",
      tipo: "gate_pendiente",
      titulo: "GATE 1 · Facturación por convenio corporativo",
      cuerpo: "Spec v1 lista para tu aprobación.",
      ticket_id: "tk_1003",
      ticket_numero: "KC-1003",
      creada_en: FECHA,
      leida: false,
    },
    {
      id: "nt_2",
      tipo: "aviso_cuota",
      titulo: "Llevas el 80 % de la cuota mensual",
      cuerpo: null,
      ticket_id: null,
      creada_en: FECHA,
      leida: true,
    },
  ],
  no_leidas: 1,
} satisfies Bandeja;

export const spec: Spec = {
  version: 1,
  estado: "pendiente_gate1",
  alternativas: [
    {
      id: "A",
      titulo: "Tabla de convenios",
      descripcion: "Nueva tabla y regla de precio",
      riesgo: "bajo",
      recomendada: true,
      elegida: false,
    },
    {
      id: "B",
      titulo: "Descuento por cliente",
      descripcion: "Reutilizar descuentos",
      riesgo: "medio",
      recomendada: false,
      elegida: false,
    },
  ],
  criterios: [
    {
      id: "C1",
      given: "un cliente con convenio",
      when: "se factura",
      then: "aplica la tarifa del convenio",
    },
  ],
  scope_paths: ["billing/"],
  estimacion_loc: 320,
};

/** Origen de las URLs firmadas del doble (el blob real es otro servicio). */
export const BLOB = "http://blob.test";

/** Evidencia con enlace firmado, como se espera que llegue con KC-BE-08-5. */
function enlaceDemo(tipo: string, archivo: string) {
  const blob_ref = `evidencia/tk_1001/r1/${archivo}`;
  return {
    tipo,
    blob_ref,
    download_url: `${BLOB}/${blob_ref}?sig=msw`,
    expira_en: "2099-01-01T00:00:00Z",
  };
}

export const evidencia: Evidencia = {
  corrida: 1,
  ejecutada_en: FECHA,
  // Testing guarda capturas, video y traza solo de lo que falla (playwright_hooks).
  matriz: [
    { criterio_id: "C1", resultado: "pasa", extracto_fallo: null, evidencias: [] },
    {
      criterio_id: "C2",
      resultado: "falla",
      extracto_fallo: "Esperaba el botón «Exportar» y no apareció.",
      evidencias: [
        enlaceDemo("screenshot", "c2.png"),
        enlaceDemo("video", "c2.webm"),
        enlaceDemo("trace", "c2_trace.zip"),
      ],
    },
  ],
  regresion: { dirigidos: 12, dirigidos_ok: 12, suite_cliente: 40, cliente_ok: 40 },
  flaky: [],
};

export const hallazgos: Hallazgos = { hallazgos: [], bloquea_deploy: false };

export const consumoTicket: ConsumoTicket = {
  totales: { tokens_in: 1200, tokens_out: 800, invocaciones: 3, loc_netas: 0, loc_tests: 0 },
  por_funcion: [
    {
      funcion: "triage.classify",
      modelo: "deepseek-v4-flash",
      invocaciones: 1,
      tokens_in: 400,
      tokens_out: 200,
      duracion_media_ms: 900,
    },
  ],
  desglose_loc: { anadidas: 0, eliminadas: 0, netas: 0, tests: 0 },
};

export const actividad: Actividad = {
  items: [
    {
      fecha: FECHA,
      actor: { tipo: "humano", nombre: "María G." },
      evento: "ticket.creado",
      etapa: "triage",
    },
  ],
  // Como el backend real: paginado sin `total` (lo detectó el esquema, KC-PT-04-2).
  page: 1,
  page_size: 50,
};

export const eventosAuditoria = {
  items: [
    {
      id: 1,
      fecha: FECHA,
      ticket_id: "KC-1001",
      etapa: "triage",
      actor_tipo: "humano",
      actor_id: "u_dev",
      evento: "ticket.creado",
      modelo: null,
      sello: "a1b2c3",
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
  cadena: { eventos_hoy: 1 },
};

export const expediente = {
  solicitante: { nombre: "María G.", origen: "portal", fecha: FECHA },
  integridad: { integra: true, hash_raiz: "a1b2c3" },
  firmas: [],
  etapas: [],
  // §11.2. El backend real aún no lo envía (KC-BE-08-6).
  cambios: [
    {
      archivo: "billing_service/exporter.py",
      pr_url: "https://github.com/demo/app/pull/479",
      racional: "El timeout venía de cargar el reporte completo en memoria; se pagina por lotes.",
    },
  ],
  // Lo que envía hoy el backend (transitorio hasta KC-BE-08-6).
  eventos: [
    {
      id: 1,
      creado_en: FECHA,
      actor_tipo: "humano",
      actor_id: "u_dev",
      resumen: "Ticket creado",
      sello: "a1b2c3",
    },
  ],
};

export const conexiones = {
  conexiones: [
    {
      id: "cn_1",
      categoria: "repositorio",
      proveedor: "github",
      nombre: "demo/app",
      estado: "conectada",
      ultimo_test: FECHA,
    },
  ],
  mapa_ramas: { feature: "feature/*", staging: "develop", produccion: "main" },
};

export const proveedoresLLM = {
  modalidad: "gestionada",
  tiers: [
    { tier: "flash", modelo: "deepseek-v4-flash", nombre: "DeepSeek V4 Flash", perfil: "simple" },
  ],
};

export const asignacionesLLM: AsignacionesLLM = {
  politica_escalado: "flash → pro → fable",
  asignaciones: [
    {
      paso: "triage.classify",
      etapa: "triage",
      tier: "flash",
      modelo: "deepseek-v4-flash",
      escalable: true,
      descripcion: "Clasifica el ticket",
      version: "1",
    },
  ],
};

export const resumenConsumo: UsageSummary = {
  ciclo: "2026-09",
  plan: { codigo: "growth", loc_mes: 20000, lineas: 3, renueva_el: "2026-10-01" },
  loc: { consumidas: 4000, restantes: 16000, porcentaje: 20 },
  tickets: { cerrados: 0, en_proceso: 3 },
  umbral: "normal",
};

export const consumoPorTicket = paginar([
  {
    ticket_id: "KC-1003",
    titulo: "Facturación por convenio corporativo",
    tipo: "funcionalidad",
    loc_netas: 0,
    tokens: 2000,
    etapa: "design",
  },
]);
