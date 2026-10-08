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

  it.each([
    ["PERMISO_DENEGADO", "You don't have permission for this operation."],
    ["FUNCION_NO_DISPONIBLE", "This feature is not included in your plan."],
    ["FIRMA_NO_AUTORIZADA", "Your role can't sign this gate."],
  ])("traduce la denegación %s (KC-PT-03-6)", (codigo, esperado) => {
    renderCaja(new ApiError(403, { codigo, mensaje: "Mensaje del servidor." }), "en");

    expect(screen.getByText(new RegExp(esperado))).toBeInTheDocument();
    expect(screen.queryByText(/Mensaje del servidor/)).not.toBeInTheDocument();
  });

  it("los errores del backend conservan el mensaje que envía el servidor", () => {
    renderCaja(
      new ApiError(404, { codigo: "NO_ENCONTRADO", mensaje: "El recurso no existe." }),
      "en",
    );

    expect(screen.getByText(/El recurso no existe\./)).toBeInTheDocument();
  });
});
