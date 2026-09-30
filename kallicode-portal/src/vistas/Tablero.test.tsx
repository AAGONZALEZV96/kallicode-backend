import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fijarSesion } from "../api/client";
import { I18nProvider } from "../estado/i18n";
import { resumenTablero } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import Tablero, { REFRESCO_TABLERO_MS } from "./Tablero";

function renderTablero() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <Tablero />
      </MemoryRouter>
    </I18nProvider>,
  );
}

const resumenCaido = http.get("*/api/v1/dashboard/summary", () =>
  sobreError(503, "SERVICIO_NO_DISPONIBLE", "Servicio no disponible."),
);

beforeEach(() => fijarSesion("acc_1", "ref_1"));

describe("Tablero: carga inicial", () => {
  it("si falla, muestra el error en vez de quedarse en «Cargando…» y permite reintentar", async () => {
    server.use(resumenCaido);
    renderTablero();

    expect(await screen.findByText("SERVICIO_NO_DISPONIBLE")).toBeInTheDocument();
    expect(screen.queryByText("Cargando…")).not.toBeInTheDocument();

    server.resetHandlers();
    await userEvent.click(screen.getByTestId("tablero.carga.reintentar"));

    expect(await screen.findByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
  });
});

describe("Tablero: refresco periódico", () => {
  beforeEach(() =>
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    }),
  );
  afterEach(() => vi.useRealTimers());

  it("si un refresco falla, conserva los datos y avisa; el aviso se va al recuperarse", async () => {
    renderTablero();
    expect(await screen.findByRole("link", { name: /KC-1001/ })).toBeInTheDocument();

    server.use(resumenCaido);
    await vi.advanceTimersByTimeAsync(REFRESCO_TABLERO_MS);

    expect(await screen.findByTestId("tablero.refresco.aviso")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /KC-1001/ })).toBeInTheDocument();

    server.resetHandlers();
    await vi.advanceTimersByTimeAsync(REFRESCO_TABLERO_MS);

    await vi.waitFor(() =>
      expect(screen.queryByTestId("tablero.refresco.aviso")).not.toBeInTheDocument(),
    );
  });

  it("un refresco lento no dispara otro en paralelo", async () => {
    renderTablero();
    expect(await screen.findByRole("link", { name: /KC-1001/ })).toBeInTheDocument();

    let llamadas = 0;
    let liberar = () => {};
    server.use(
      http.get("*/api/v1/dashboard/summary", async () => {
        llamadas++;
        await new Promise<void>((r) => (liberar = r));
        return HttpResponse.json(resumenTablero);
      }),
    );

    await vi.advanceTimersByTimeAsync(REFRESCO_TABLERO_MS);
    await vi.waitFor(() => expect(llamadas).toBe(1));

    // Pasan dos intervalos más con la petición colgada: no debe salir ninguna nueva.
    await vi.advanceTimersByTimeAsync(REFRESCO_TABLERO_MS * 2);
    expect(llamadas).toBe(1);

    liberar();
  });
});
