/**
 * Las vistas que tenían textos fijos en español, en inglés (KC-PT-10-3): la spec, la evidencia,
 * la seguridad y el consumo del ticket, Consumo, Conexiones y Modelos IA.
 */
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

const texto = (el: HTMLElement) => (el.textContent ?? "").replace(/\s/g, " ");

beforeEach(() => localStorage.setItem("kc_idioma", "en"));

describe("detalle del ticket en inglés", () => {
  async function pestana(zona: string) {
    const usuario = await entrarDesde("/tickets/KC-1001");
    await usuario.click(await screen.findByTestId(`detalle-ticket.pestanas.${zona}`));
  }

  it("spec: versión, estado y criterios", async () => {
    await pestana("spec");

    const spec = await screen.findByTestId("detalle-ticket.spec.contenido");
    expect(texto(spec)).toMatch(/Spec v\d+ · (awaiting Gate 1|approved|replaced|rejected)/);
    expect(spec).toHaveTextContent(
      "Given un cliente con convenio, when se factura, then aplica la tarifa del convenio.",
    );
  });

  it("evidencia: encabezados, resultado y regresión", async () => {
    await pestana("evidencia");

    const tabla = await screen.findByTestId("detalle-ticket.evidencia.tabla");
    expect(within(tabla).getByRole("columnheader", { name: "Result" })).toBeInTheDocument();
    expect(texto(tabla)).toMatch(/✓ PASS|✗ FAIL|~ FLAKY/);
    expect(screen.getByText(/Targeted regression: \d+\/\d+/)).toBeInTheDocument();
    expect(screen.getByText(/client suite: \d+\/\d+/)).toBeInTheDocument();
  });

  it("seguridad: severidad y estado traducidos, y el bloqueo", async () => {
    server.use(
      http.get("*/api/v1/tickets/:numero/security", () =>
        HttpResponse.json({
          hallazgos: [
            {
              id: "h1",
              severidad: "critica",
              titulo: "SQL injection",
              origen: "sast",
              estado: "abierto",
              justificacion: null,
            },
            {
              id: "h2",
              severidad: "media",
              titulo: "Dependencia vieja",
              origen: "sca",
              estado: "corregido",
              justificacion: null,
            },
          ],
          bloquea_deploy: true,
        }),
      ),
    );
    await pestana("seguridad");

    const tabla = await screen.findByTestId("detalle-ticket.seguridad.tabla");
    expect(within(tabla).getByRole("columnheader", { name: "Severity" })).toBeInTheDocument();
    expect(tabla).toHaveTextContent("Critical");
    expect(tabla).toHaveTextContent("Open");
    expect(tabla).toHaveTextContent("Medium");
    expect(within(tabla).getByTitle("Fixed")).toBeInTheDocument();
    expect(screen.getByTestId("detalle-ticket.seguridad.bloqueo")).toHaveTextContent(
      "Unresolved high severity: it blocks the Deploy.",
    );
  });

  it("consumo: indicadores y columnas", async () => {
    await pestana("consumo");

    const tabla = await screen.findByTestId("detalle-ticket.consumo.tabla");
    expect(screen.getByText("Total tokens")).toBeInTheDocument();
    expect(screen.getByText("Invocations")).toBeInTheDocument();
    for (const columna of ["Function", "Model", "Input tokens", "Output tokens"])
      expect(within(tabla).getByRole("columnheader", { name: columna })).toBeInTheDocument();
  });
});

describe("otras vistas en inglés", () => {
  it("Consumo: cuota, renovación y consumo por ticket", async () => {
    await entrarDesde("/consumo");

    const barra = await screen.findByTestId("consumo.cuota.barra");
    expect(document.getElementById(barra.getAttribute("aria-labelledby")!)).toHaveTextContent(
      /Monthly LOC quota · 20,000 LOC\/month/,
    );
    expect(screen.getByText(/Renews on/)).toBeInTheDocument();
    expect(screen.getByText(/Usage per ticket \(cycle 2026-09\)/)).toBeInTheDocument();
  });

  it("Conexiones: estado traducido, botón y mapa de ramas", async () => {
    await entrarDesde("/conexiones");

    const estado = await screen.findByTestId("conexiones.lista.estado");
    expect(estado).toHaveTextContent("● Connected");
    expect(screen.getByTestId("conexiones.lista.probar")).toHaveTextContent("Test");
    expect(screen.getByText("Branch map")).toBeInTheDocument();
    expect(screen.getByLabelText("production →")).toHaveValue("main");
  });

  it("Modelos IA: encabezados de la tabla", async () => {
    await entrarDesde("/modelos");

    const tabla = await screen.findByTestId("modelos-ia.asignaciones.tabla");
    for (const columna of ["Step", "Model", "Description"])
      expect(within(tabla).getByRole("columnheader", { name: columna })).toBeInTheDocument();
  });

  it("un error del catálogo sale en el idioma del portal, no con el mensaje del backend", async () => {
    server.use(
      http.get("*/api/v1/usage/summary", () =>
        sobreError(429, "RATE_LIMIT_EXCEDIDO", "Se superó el límite de peticiones."),
      ),
    );
    await entrarDesde("/consumo");

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("Too many requests in a row. Wait a moment and try again.");
    expect(alerta).not.toHaveTextContent("Se superó");
  });
});
