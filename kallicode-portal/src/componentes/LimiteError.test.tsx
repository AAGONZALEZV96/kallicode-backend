import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { ApiError } from "../api/client";
import { AuthProvider } from "../estado/auth";
import { I18nProvider } from "../estado/i18n";
import { Proveedores } from "../test/portal";
import Layout from "./Layout";
import LimiteError from "./LimiteError";

let explotar = true;
let errorALanzar: unknown = new Error("fallo de render");

function Bomba() {
  if (explotar) throw errorALanzar;
  return <p>contenido ok</p>;
}

/** Eventos que el límite pasó a reportarErrorCliente (salen por console.error). */
function reportados(consola: MockInstance) {
  return consola.mock.calls
    .filter((c: string[]) => c[0] === "kc.error_cliente")
    .map((c: unknown[]) => c[1] as { id: string; traceId?: string });
}

let consola: MockInstance;

beforeEach(() => {
  explotar = true;
  errorALanzar = new Error("fallo de render");
  // React también escribe el error en consola; se silencia para no ensuciar la salida.
  consola = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => consola.mockRestore());

describe("LimiteError", () => {
  it("muestra la pantalla de recuperación y reporta el error con su pila", () => {
    render(
      <I18nProvider>
        <LimiteError nivel="ruta">
          <Bomba />
        </LimiteError>
      </I18nProvider>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Algo salió mal");
    const [evento] = reportados(consola);
    expect(evento).toMatchObject({
      nivel: "ruta",
      mensaje: "fallo de render",
      id: expect.stringMatching(/^err_/),
      pilaComponentes: expect.stringContaining("Bomba"),
    });
    expect(screen.getByText(new RegExp(evento.id))).toBeInTheDocument();
  });

  it("incluye el trace_id cuando el error viene de la API", () => {
    errorALanzar = new ApiError(500, { codigo: "ERROR_INTERNO", mensaje: "x", trace_id: "tr_1" });
    render(
      <LimiteError nivel="ruta">
        <Bomba />
      </LimiteError>,
    );

    expect(reportados(consola)[0]).toMatchObject({ traceId: "tr_1" });
  });

  it("Reintentar vuelve a renderizar el contenido", async () => {
    render(
      <I18nProvider>
        <LimiteError nivel="ruta">
          <Bomba />
        </LimiteError>
      </I18nProvider>,
    );

    explotar = false;
    await userEvent.click(screen.getByTestId("error.ruta.reintentar"));

    expect(screen.getByText("contenido ok")).toBeInTheDocument();
  });

  it("el nivel raíz ofrece volver al inicio", () => {
    render(
      <I18nProvider>
        <LimiteError nivel="raiz">
          <Bomba />
        </LimiteError>
      </I18nProvider>,
    );

    expect(screen.getByTestId("error.raiz.pantalla")).toBeInTheDocument();
    expect(screen.getByTestId("error.raiz.inicio")).toHaveAttribute("href", "/");
  });

  it("en el Layout, el error de una vista no tumba la navegación y se limpia al navegar", async () => {
    render(
      <Proveedores>
        <AuthProvider>
          <MemoryRouter initialEntries={["/tickets"]}>
            <Routes>
              <Route element={<Layout />}>
                <Route path="/" element={<p>vista tablero</p>} />
                <Route path="/tickets" element={<Bomba />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </Proveedores>,
    );

    expect(screen.getByTestId("error.ruta.pantalla")).toBeInTheDocument();
    const enlaceTablero = screen.getByRole("link", { name: /Tablero/ });

    await userEvent.click(enlaceTablero);

    expect(screen.queryByTestId("error.ruta.pantalla")).not.toBeInTheDocument();
    expect(screen.getByText("vista tablero")).toBeInTheDocument();
  });
});
