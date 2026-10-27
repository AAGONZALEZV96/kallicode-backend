import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { fijarSesion } from "../api/client";
import { expediente } from "../mocks/datos";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import Expediente from "./Expediente";

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

/** Copia del expediente de prueba sin un campo (simula respuestas del backend actual o futuro). */
function sinCampo(campo: keyof typeof expediente) {
  const copia: Partial<typeof expediente> = { ...expediente };
  delete copia[campo];
  return copia;
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

  it("con el backend actual (sin cambios) muestra el estado vacío y conserva los eventos", async () => {
    responderExpediente(sinCampo("cambios"));
    renderExpediente();

    expect(await screen.findByTestId("expediente.cambios.vacio")).toBeInTheDocument();
    expect(screen.getByText("Eventos")).toBeInTheDocument();
    expect(screen.getByText("Ticket creado")).toBeInTheDocument();
  });

  it("cuando el backend deje de enviar eventos, la sección desaparece", async () => {
    responderExpediente(sinCampo("eventos"));
    renderExpediente();

    expect(await screen.findByText("billing_service/exporter.py")).toBeInTheDocument();
    expect(screen.queryByText("Eventos")).not.toBeInTheDocument();
  });

  it("no enlaza pr_url que no sean http(s)", async () => {
    responderExpediente({
      ...expediente,
      cambios: [{ archivo: "x.py", pr_url: "javascript:alert(1)", racional: "r" }],
    });
    renderExpediente();

    expect(await screen.findByText("x.py")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Ver PR/ })).not.toBeInTheDocument();
  });
});
