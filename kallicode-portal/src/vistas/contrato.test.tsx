/**
 * Cada vista valida en la frontera lo que muestra (KC-PT-04-2, cierra PT-F09): con una
 * respuesta fuera de contrato muestra RESPUESTA_INVALIDA en vez de renderizar datos que
 * no cumplen el tipo. Si un hook deja de pasar su esquema, esta suite lo detecta.
 */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { server } from "../mocks/server";
import { entrarDesde, renderPortal, rutaActual } from "../test/portal";

beforeEach(() => {
  // La divergencia se registra en consola (telemetría): aquí es lo esperado.
  const consola = vi.spyOn(console, "error").mockImplementation(() => {});
  onTestFinished(() => consola.mockRestore());
});

const fueraDeContrato = (ruta: string) =>
  server.use(http.get(`*${ruta}`, () => HttpResponse.json({ inesperado: true })));

describe.each([
  ["/", "/api/v1/dashboard/summary"],
  ["/tickets", "/api/v1/tickets"],
  ["/tickets/KC-1001", "/api/v1/tickets/:numero"],
  ["/aprobaciones", "/api/v1/approvals/pending"],
  ["/auditoria", "/api/v1/audit/events"],
  ["/auditoria/KC-1001", "/api/v1/audit/tickets/:numero"],
  ["/conexiones", "/api/v1/connections"],
  ["/modelos", "/api/v1/models/providers"],
  ["/modelos", "/api/v1/models/assignments"],
  ["/consumo", "/api/v1/usage/summary"],
  ["/consumo", "/api/v1/usage/by-ticket"],
])("vista %s", (rutaPortal, rutaApi) => {
  it(`rechaza ${rutaApi} fuera de contrato`, async () => {
    fueraDeContrato(rutaApi);

    await entrarDesde(rutaPortal);

    expect(await screen.findByText(/RESPUESTA_INVALIDA/)).toBeInTheDocument();
  });
});

describe("sesión", () => {
  it.each([
    ["el login", "POST", "/api/v1/auth/login"],
    ["el perfil (/users/me)", "GET", "/api/v1/users/me"],
  ])("rechaza %s fuera de contrato y no entra", async (_nombre, metodo, ruta) => {
    server.use(
      http[metodo === "POST" ? "post" : "get"](`*${ruta}`, () =>
        HttpResponse.json({ inesperado: true }),
      ),
    );
    const { correo, clave } = renderPortal();
    const usuario = userEvent.setup();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo{Enter}");

    expect(await screen.findByText(/RESPUESTA_INVALIDA/)).toBeInTheDocument();
    expect(rutaActual()).toBe("/login");
  });
});

describe.each([
  ["Spec", "spec"],
  ["Evidencia QA", "evidence"],
  ["Seguridad", "security"],
  ["Consumo", "usage"],
  ["Actividad", "activity"],
])("pestaña %s del ticket", (nombre, ruta) => {
  it(`rechaza /tickets/{numero}/${ruta} fuera de contrato`, async () => {
    fueraDeContrato(`/api/v1/tickets/:numero/${ruta}`);
    const usuario = await entrarDesde("/tickets/KC-1001");

    await usuario.click(await screen.findByRole("button", { name: nombre }));

    expect(await screen.findByText(/RESPUESTA_INVALIDA/)).toBeInTheDocument();
  });
});
