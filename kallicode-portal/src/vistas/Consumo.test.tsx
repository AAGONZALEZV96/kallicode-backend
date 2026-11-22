/** Umbral de consumo traducido, no el código crudo del backend (KC-PT-03-6). */
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { resumenConsumo } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

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
