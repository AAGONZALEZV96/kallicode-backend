/**
 * Contrato de los dobles: cada endpoint que consume el portal tiene un doble MSW.
 * Si una vista empieza a usar una ruta nueva, agrégala aquí y en handlers.ts.
 */
import { describe, expect, it } from "vitest";

import { api, fijarSesion } from "../api/client";

type Metodo = "get" | "post" | "patch";

const ENDPOINTS: [Metodo, string][] = [
  ["get", "/api/v1/users/me"],
  ["get", "/api/v1/organization"],
  ["get", "/api/v1/dashboard/summary"],
  ["get", "/api/v1/dashboard/lines"],
  ["get", "/api/v1/dashboard/kanban"],
  ["get", "/api/v1/tickets?page=1"],
  ["post", "/api/v1/tickets"],
  ["post", "/api/v1/tickets/attachments"],
  ["post", "/api/v1/tickets/dedup-preview"],
  ["post", "/api/v1/tickets/impact-preview"],
  ["post", "/api/v1/tickets/features"],
  ["get", "/api/v1/tickets/KC-1001"],
  ["patch", "/api/v1/tickets/KC-1001"],
  ["get", "/api/v1/tickets/KC-1001/spec"],
  ["get", "/api/v1/tickets/KC-1001/evidence"],
  ["get", "/api/v1/tickets/KC-1001/security"],
  ["get", "/api/v1/tickets/KC-1001/usage"],
  ["get", "/api/v1/tickets/KC-1001/activity?page_size=50"],
  ["post", "/api/v1/tickets/KC-1003/gates/1/approve"],
  ["get", "/api/v1/approvals/pending"],
  ["get", "/api/v1/notifications?page_size=1"],
  ["get", "/api/v1/audit/events?page=1"],
  ["get", "/api/v1/audit/tickets/KC-1001"],
  ["get", "/api/v1/connections"],
  ["post", "/api/v1/connections/cn_1/test"],
  ["post", "/api/v1/onboarding/complete"],
  ["get", "/api/v1/models/providers"],
  ["get", "/api/v1/models/assignments"],
  ["get", "/api/v1/usage/summary"],
  ["get", "/api/v1/usage/by-ticket"],
  ["post", "/api/v1/plans/upgrade-request"],
];

function llamar(metodo: Metodo, ruta: string) {
  return metodo === "get" ? api.get(ruta) : api[metodo](ruta, {});
}

describe("dobles MSW del portal", () => {
  it.each(ENDPOINTS)("%s %s responde con sesión", async (metodo, ruta) => {
    fijarSesion("acc_1", "ref_1");
    await expect(llamar(metodo, ruta)).resolves.toBeDefined();
  });

  it("login y refresh (sin sesión previa) también tienen doble", async () => {
    await expect(
      api.post("/api/v1/auth/login", {
        email: "owner@demo.kallicode.dev",
        password: "Dev12345!demo",
      }),
    ).resolves.toMatchObject({ access_token: "acc_1" });
  });

  it("un ticket inexistente responde 404", async () => {
    fijarSesion("acc_1", "ref_1");
    await expect(api.get("/api/v1/tickets/KC-9999/spec")).rejects.toMatchObject({ http: 404 });
  });
});
