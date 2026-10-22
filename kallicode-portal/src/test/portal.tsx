/** Utilidades de prueba: el portal completo con router en memoria y login como owner. */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent, { type Options } from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { crearClienteConsultas } from "../api/consultas";
import App from "../App";
import { AuthProvider } from "../estado/auth";
import { I18nProvider } from "../estado/i18n";

/**
 * Idioma y caché de servidor para montar una vista suelta. Cada render estrena su
 * QueryClient: ninguna prueba hereda datos en caché de otra.
 */
export function Proveedores({ children }: { children: ReactNode }) {
  const [cliente] = useState(crearClienteConsultas);
  return (
    <I18nProvider>
      <QueryClientProvider client={cliente}>{children}</QueryClientProvider>
    </I18nProvider>
  );
}

/** Expone la ruta actual para comprobar a dónde llevan las redirecciones. */
function RutaActual() {
  const { pathname, search } = useLocation();
  return <output data-testid="prueba.ruta.actual">{pathname + search}</output>;
}

export function renderPortal(ruta = "/") {
  const { container } = render(
    <Proveedores>
      <AuthProvider>
        <MemoryRouter initialEntries={[ruta]}>
          <App />
          <RutaActual />
        </MemoryRouter>
      </AuthProvider>
    </Proveedores>,
  );
  // Las etiquetas del login aún no están asociadas a su input (KC-PT-05-4).
  const campo = (tipo: string) => {
    const el = container.querySelector<HTMLInputElement>(`input[type="${tipo}"]`);
    if (!el) throw new Error(`No hay input[type=${tipo}]`);
    return el;
  };
  return { correo: campo("email"), clave: campo("password") };
}

/**
 * Abre el portal en `ruta` e inicia sesión como owner (en el idioma de `kc_idioma`). Con temporizadores simulados,
 * pasar `{ advanceTimers: vi.advanceTimersByTime }` para que userEvent no se quede esperando.
 */
export async function entrarDesde(ruta = "/", opciones?: Options) {
  const usuario = userEvent.setup(opciones);
  const { correo, clave } = renderPortal(ruta);
  await usuario.type(correo, "owner@demo.kallicode.dev");
  // Enter envía el formulario en cualquier idioma (el botón cambia de nombre).
  await usuario.type(clave, "Dev12345!demo{Enter}");
  return usuario;
}

export const rutaActual = () => screen.getByTestId("prueba.ruta.actual").textContent;
