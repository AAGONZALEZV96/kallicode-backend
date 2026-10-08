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
