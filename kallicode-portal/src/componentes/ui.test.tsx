import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { I18nProvider } from "../estado/i18n";
import { CajaError } from "./ui";

function renderCaja(error: unknown, idioma: string) {
  localStorage.setItem("kc_idioma", idioma);
  render(
    <I18nProvider>
      <CajaError error={error} />
    </I18nProvider>,
  );
}

describe("CajaError", () => {
  it("muestra los errores de red del cliente en el idioma del usuario", () => {
    renderCaja(new ApiError(0, { codigo: "SIN_CONEXION", mensaje: "No se pudo conectar." }), "en");

    expect(screen.getByText(/Could not reach the server/)).toBeInTheDocument();
  });

  it("los errores del backend conservan el mensaje que envía el servidor", () => {
    renderCaja(
      new ApiError(404, { codigo: "NO_ENCONTRADO", mensaje: "El recurso no existe." }),
      "en",
    );

    expect(screen.getByText(/El recurso no existe\./)).toBeInTheDocument();
  });
});
