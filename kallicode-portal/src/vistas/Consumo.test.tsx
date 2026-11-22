/** Umbral de consumo traducido, no el código crudo del backend (KC-PT-03-6). */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { resumenConsumo } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

describe("Consumo: umbral", () => {
  it.each([
    ["es", "aviso_80", "Sobre el 80 %"],
    ["en", "agotado", "Exhausted"],
  ])("en %s muestra %s como «%s»", async (idioma, umbral, texto) => {
    localStorage.setItem("kc_idioma", idioma);
    server.use(
      http.get("*/api/v1/usage/summary", () => HttpResponse.json({ ...resumenConsumo, umbral })),
    );
    await entrarDesde("/consumo");

    expect(await screen.findByText(texto)).toBeInTheDocument();
    expect(screen.queryByText(umbral)).not.toBeInTheDocument();
  });
});

describe("Consumo: números y fechas en el idioma del portal (KC-PT-09-3)", () => {
  /** Intl separa con espacios no separables: se comparan como espacios. */
  const sinNbsp = (texto: string | null) => (texto ?? "").replace(/\s/g, " ");

  it.each([
    ["es", "4000 / 20.000 LOC · 20 %", "Se renueva el 1 oct 2026", "4000", "16.000"],
    ["en", "4,000 / 20,000 LOC · 20%", "Se renueva el Oct 1, 2026", "4,000", "16,000"],
  ])("en %s", async (idioma, cuota, renovacion, consumidas, restantes) => {
    localStorage.setItem("kc_idioma", idioma);
    await entrarDesde("/consumo");

    const barra = await screen.findByTestId("consumo.cuota.barra");
    const pie = barra.nextElementSibling!;
    expect(sinNbsp(pie.textContent)).toBe(`${cuota}${renovacion}`);
    expect(screen.getByTestId("consumo.kpis.consumidas")).toHaveTextContent(consumidas);
    expect(screen.getByTestId("consumo.kpis.restantes")).toHaveTextContent(restantes);
  });
});

describe("Consumo: tiempo e2e de fábrica y orden (KC-PT-09-2)", () => {
  /** Los sort que el portal pide a GET /usage/by-ticket. */
  function ordenesPedidos() {
    const sorts: (string | null)[] = [];
    server.events.on("request:start", ({ request }) => {
      const u = new URL(request.url);
      if (u.pathname === "/api/v1/usage/by-ticket") sorts.push(u.searchParams.get("sort"));
    });
    return sorts;
  }
  const filas = () =>
    screen.getAllByTestId("consumo.tickets.fila").map((f) => f.querySelector("td")!.textContent);
  const encabezado = (clave: string) => screen.getByTestId(`consumo.tickets.orden-${clave}`);

  it("cada ticket muestra su tiempo e2e; el que sigue en curso lo dice", async () => {
    await entrarDesde("/consumo");
    const tabla = await screen.findByTestId("consumo.tickets.tabla");

    const curso = within(tabla).getByText("KC-1003").closest("tr")!;
    expect(curso).toHaveTextContent("2h 41min · en curso");
    expect(within(curso).getByTitle("2 horas y 41 minutos")).toBeInTheDocument();
    const cerrado = within(tabla).getByText("KC-1001").closest("tr")!;
    expect(cerrado).toHaveTextContent("3d 4h");
    expect(cerrado).not.toHaveTextContent("en curso");
  });

  it("el KPI muestra la mediana de los cerrados del ciclo", async () => {
    await entrarDesde("/consumo");

    const kpi = await screen.findByTestId("consumo.kpis.tiempo-e2e");
    expect(kpi).toHaveTextContent("Tiempo de fábrica");
    expect(kpi).toHaveTextContent("2h 41min");
    expect(kpi).toHaveTextContent("mediana de 2 cerrados en el ciclo");
  });

  it("sin cerrados en el ciclo, el KPI muestra «—»", async () => {
    server.use(
      http.get("*/api/v1/usage/summary", () =>
        HttpResponse.json({ ...resumenConsumo, tiempo_e2e: { mediana_s: null, cerrados: 0 } }),
      ),
    );
    await entrarDesde("/consumo");

    expect(await screen.findByTestId("consumo.kpis.tiempo-e2e")).toHaveTextContent(
      "—mediana de 0 cerrados en el ciclo",
    );
  });

  it("ordena por tiempo: primero de mayor a menor, otro clic invierte; queda en la URL", async () => {
    const sorts = ordenesPedidos();
    const usuario = await entrarDesde("/consumo?page=2");
    await screen.findByTestId("consumo.tickets.tabla");
    expect(encabezado("tiempo").closest("th")).toHaveAttribute("aria-sort", "none");

    await usuario.click(encabezado("tiempo"));

    await waitFor(() => expect(filas()).toEqual(["KC-1001", "KC-1003", "KC-1002"]));
    expect(encabezado("tiempo").closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(rutaActual()).toBe("/consumo?orden=-tiempo_e2e");

    await usuario.click(encabezado("tiempo"));

    await waitFor(() => expect(filas()).toEqual(["KC-1002", "KC-1003", "KC-1001"]));
    expect(encabezado("tiempo").closest("th")).toHaveAttribute("aria-sort", "ascending");
    expect(rutaActual()).toBe("/consumo?orden=tiempo_e2e");
    expect(sorts).toEqual([null, "-tiempo_e2e", "tiempo_e2e"]);
  });

  it("también por tokens y LOC; solo una columna marca el orden", async () => {
    const sorts = ordenesPedidos();
    const usuario = await entrarDesde("/consumo");
    await screen.findByTestId("consumo.tickets.tabla");

    await usuario.click(encabezado("tokens"));
    await waitFor(() => expect(filas()[0]).toBe("KC-1001"));
    await usuario.click(encabezado("loc"));
    await waitFor(() => expect(sorts.at(-1)).toBe("-loc_netas"));

    expect(encabezado("loc").closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(encabezado("tokens").closest("th")).toHaveAttribute("aria-sort", "none");
  });

  it("un orden inválido en la URL se ignora (no se manda al backend)", async () => {
    const sorts = ordenesPedidos();
    await entrarDesde("/consumo?orden=titulo;DROP");
    await screen.findByTestId("consumo.tickets.tabla");

    expect(sorts).toEqual([null]);
  });
});
