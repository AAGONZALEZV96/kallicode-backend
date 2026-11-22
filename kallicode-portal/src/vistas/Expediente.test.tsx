import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fijarSesion } from "../api/client";
import { expediente } from "../mocks/datos";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import Expediente from "./Expediente";

// Sin AuthProvider: estas pruebas miran el contenido del expediente. La exportación y quién
// la ve (KC-PT-08-3) se prueban en el portal completo, en Exportaciones.test.tsx.
vi.mock("../estado/auth", async (original) => ({
  ...(await original<typeof import("../estado/auth")>()),
  usePermiso: () => ({ puedeHacer: () => false, tieneAccesoARuta: () => true }),
}));

function renderExpediente() {
  render(
    <Proveedores>
      <MemoryRouter initialEntries={["/auditoria/KC-1001"]}>
        <Routes>
          <Route path="/auditoria/:numero" element={<Expediente />} />
        </Routes>
      </MemoryRouter>
    </Proveedores>,
  );
}

function responderExpediente(cuerpo: object) {
  server.use(http.get("*/api/v1/audit/tickets/:numero", () => HttpResponse.json(cuerpo)));
}

beforeEach(() => fijarSesion("acc_1", "ref_1"));

describe("Expediente", () => {
  it("muestra los cambios de código de §11.2 con su racional y enlace al PR", async () => {
    renderExpediente();

    expect(await screen.findByText("billing_service/exporter.py")).toBeInTheDocument();
    expect(screen.getByText(/se pagina por lotes/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Ver PR/ })).toHaveAttribute(
      "href",
      "https://github.com/demo/app/pull/479",
    );
  });

  it("con cambios vacíos muestra el estado vacío y conserva los eventos", async () => {
    responderExpediente({ ...expediente, cambios: [] });
    renderExpediente();

    expect(await screen.findByTestId("expediente.cambios.vacio")).toBeInTheDocument();
    expect(screen.getByText("Eventos")).toBeInTheDocument();
    expect(screen.getByText("Ticket creado")).toBeInTheDocument();
  });

  it("con eventos vacíos oculta la sección de eventos", async () => {
    responderExpediente({ ...expediente, eventos: [] });
    renderExpediente();

    expect(await screen.findByText("billing_service/exporter.py")).toBeInTheDocument();
    expect(screen.queryByText("Eventos")).not.toBeInTheDocument();
  });

  it("no enlaza pr_url que no sean http(s)", async () => {
    responderExpediente({
      ...expediente,
      cambios: [{ ...expediente.cambios[0], archivo: "x.py", pr_url: "javascript:alert(1)" }],
    });
    renderExpediente();

    expect(await screen.findByText("x.py")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Ver PR/ })).not.toBeInTheDocument();
  });
});
