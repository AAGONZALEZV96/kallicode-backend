/** Recorrido de humo: login contra los dobles MSW y llegada al tablero. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "./App";
import { AuthProvider } from "./estado/auth";
import { I18nProvider } from "./estado/i18n";

function renderPortal() {
  const { container } = render(
    <I18nProvider>
      <AuthProvider>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </I18nProvider>,
  );
  // Las etiquetas del login aún no están asociadas a su input (KC-PT-05-4).
  const campo = (tipo: string) => {
    const el = container.querySelector<HTMLInputElement>(`input[type="${tipo}"]`);
    if (!el) throw new Error(`No hay input[type=${tipo}]`);
    return el;
  };
  return { correo: campo("email"), clave: campo("password") };
}

describe("portal", () => {
  it("inicia sesión y muestra el tablero con el kanban", async () => {
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /KC-1001/ })).toHaveAttribute(
      "href",
      "/tickets/KC-1001",
    );
  });

  it("con credenciales inválidas muestra el código de error y el trace_id", async () => {
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "incorrecta123");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByText("AUTH_CREDENCIALES_INVALIDAS")).toBeInTheDocument();
    expect(screen.getByText(/tr_msw_prueba/)).toBeInTheDocument();
  });
});
