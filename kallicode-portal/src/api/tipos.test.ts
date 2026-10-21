/**
 * Esquemas Zod de la frontera (KC-PT-04-2): los dobles MSW deben cumplirlos.
 *
 * Si un doble se desalinea del contrato, las pruebas de vistas siguen pasando con datos
 * que el backend real no envía (así se escondían `page` y `page_size` en auditoría y
 * actividad). Esta suite lo detecta en el punto exacto.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ZodType } from "zod";

import { PASSWORD_DEMO } from "../mocks/datos";
import { api, ApiError, fijarSesion } from "./client";
import {
  ActividadEsquema,
  AprobacionesEsquema,
  AsignacionesLLMEsquema,
  ConexionesEsquema,
  ConsumoPorTicketEsquema,
  ConsumoTicketEsquema,
  EventosAuditoriaEsquema,
  EvidenciaEsquema,
  ExpedienteEsquema,
  HallazgosEsquema,
  ImpactoEsquema,
  KanbanEsquema,
  LineasEsquema,
  NotificacionesEsquema,
  OrganizacionEsquema,
  paginado,
  PerfilPropioEsquema,
  ProveedoresLLMEsquema,
  ResumenTableroEsquema,
  SesionEsquema,
  SimilaresEsquema,
  SpecEsquema,
  TicketCreadoEsquema,
  TicketDetalleEsquema,
  TicketResumenEsquema,
  UpgradeSolicitadoEsquema,
  UsageSummaryEsquema,
} from "./tipos";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

const GET: [string, ZodType][] = [
  ["/api/v1/users/me", PerfilPropioEsquema],
  ["/api/v1/organization", OrganizacionEsquema],
  ["/api/v1/dashboard/summary", ResumenTableroEsquema],
  ["/api/v1/dashboard/lines", LineasEsquema],
  ["/api/v1/dashboard/kanban", KanbanEsquema],
  ["/api/v1/approvals/pending", AprobacionesEsquema],
  ["/api/v1/notifications?page_size=1", NotificacionesEsquema],
  ["/api/v1/tickets?page=1&page_size=25", paginado(TicketResumenEsquema)],
  ["/api/v1/tickets/KC-1001", TicketDetalleEsquema],
  ["/api/v1/tickets/KC-1001/spec", SpecEsquema],
  ["/api/v1/tickets/KC-1001/evidence", EvidenciaEsquema],
  ["/api/v1/tickets/KC-1001/security", HallazgosEsquema],
  ["/api/v1/tickets/KC-1001/usage", ConsumoTicketEsquema],
  ["/api/v1/tickets/KC-1001/activity?page_size=50", ActividadEsquema],
  ["/api/v1/audit/events?page=1&page_size=50", EventosAuditoriaEsquema],
  ["/api/v1/audit/tickets/KC-1001", ExpedienteEsquema],
  ["/api/v1/connections", ConexionesEsquema],
  ["/api/v1/models/providers", ProveedoresLLMEsquema],
  ["/api/v1/models/assignments", AsignacionesLLMEsquema],
  ["/api/v1/usage/summary", UsageSummaryEsquema],
  ["/api/v1/usage/by-ticket", paginado(ConsumoPorTicketEsquema)],
];

const POST: [string, unknown, ZodType][] = [
  [
    "/api/v1/auth/login",
    { email: "owner@demo.kallicode.dev", password: PASSWORD_DEMO },
    SesionEsquema,
  ],
  [
    "/api/v1/tickets/dedup-preview",
    { texto: "error al exportar facturas", k: 3 },
    SimilaresEsquema,
  ],
  ["/api/v1/tickets/impact-preview", { texto: "exportar facturas" }, ImpactoEsquema],
  [
    "/api/v1/tickets",
    { tipo: "bug", titulo: "Falla", descripcion: "Descripción de prueba" },
    TicketCreadoEsquema,
  ],
  [
    "/api/v1/tickets/features",
    { nombre: "Exportar", objetivo: "Exportar facturas a Excel" },
    TicketCreadoEsquema,
  ],
  ["/api/v1/plans/upgrade-request", { plan_deseado: "enterprise" }, UpgradeSolicitadoEsquema],
];

describe("los dobles MSW cumplen los esquemas de la frontera", () => {
  it.each(GET)("GET %s", async (ruta, esquema) => {
    await expect(api.get(ruta, { esquema })).resolves.toBeDefined();
  });

  it.each(POST)("POST %s", async (ruta, cuerpo, esquema) => {
    await expect(api.post(ruta, cuerpo, { esquema })).resolves.toBeDefined();
  });
});

describe("validación en la frontera", () => {
  it("una respuesta fuera de contrato es un error RESPUESTA_INVALIDA con trace_id y queda registrada", async () => {
    const consola = vi.spyOn(console, "error").mockImplementation(() => {});

    const error = await api
      .get("/api/v1/dashboard/summary", { esquema: TicketDetalleEsquema })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      http: 0,
      codigo: "RESPUESTA_INVALIDA",
      traceId: expect.stringMatching(/^trw_/),
    });
    expect(consola).toHaveBeenCalledWith(
      "kc.contrato_divergente",
      expect.objectContaining({ ruta: "/api/v1/dashboard/summary" }),
    );
    consola.mockRestore();
  });

  it("devuelve solo los campos declarados: lo demás no llega a la vista", async () => {
    const r = await api.post(
      "/api/v1/plans/upgrade-request",
      { plan_deseado: "enterprise" },
      { esquema: UpgradeSolicitadoEsquema },
    );

    expect(r).toEqual({ mensaje: "El equipo comercial te contactará hoy mismo." });
  });

  it("sin esquema la respuesta pasa tal cual", async () => {
    const r = await api.post("/api/v1/plans/upgrade-request", { plan_deseado: "enterprise" });

    expect(r).toMatchObject({ solicitud_id: "upg_1", estado: "registrada" });
  });
});
