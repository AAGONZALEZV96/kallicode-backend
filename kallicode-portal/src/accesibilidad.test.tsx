/**
 * Cero violaciones de axe en las vistas del portal (KC-PT-10-2, WCAG 2.1 A y AA, más las
 * buenas prácticas de axe: una región principal, un h1, el orden de los encabezados…).
 *
 * jsdom no dibuja la página: el contraste de color lo comprueba contraste.test.ts sobre los
 * tokens, y las zonas que se desplazan, componentes/Desplazable.test.tsx. La auditoría completa,
 * con colores y tamaños reales en Chrome, está descrita en el PR de KC-PT-10-2.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "./App";
import { AuthProvider } from "./estado/auth";
import { entrarDesde, Proveedores } from "./test/portal";

/** Abre una ruta sin iniciar sesión (las pantallas públicas). */
function abrirSinSesion(ruta: string) {
  render(
    <Proveedores>
      <AuthProvider>
        <MemoryRouter initialEntries={[ruta]}>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </Proveedores>,
  );
}

/** Reglas que necesitan el layout del navegador (jsdom no lo calcula). */
const SIN_LAYOUT = {
  "color-contrast": { enabled: false },
  "scrollable-region-focusable": { enabled: false },
};

async function violaciones() {
  const r = await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
    rules: SIN_LAYOUT,
    resultTypes: ["violations"],
  });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

const VISTAS: [string, string][] = [
  ["/", "tablero"],
  ["/tickets", "tickets"],
  ["/tickets/KC-1003", "detalle-ticket"],
  ["/nuevo-ticket", "nuevo-ticket"],
  ["/nueva-funcionalidad", "nueva-funcionalidad"],
  ["/aprobaciones", "aprobaciones"],
  ["/auditoria", "auditoria"],
  ["/auditoria/KC-1001", "expediente"],
  ["/onboarding?paso=1", "onboarding"],
  ["/onboarding?paso=2", "onboarding"],
  ["/onboarding?paso=3", "onboarding"],
  ["/onboarding?paso=4", "onboarding"],
  ["/onboarding?paso=5", "onboarding"],
  ["/conexiones", "conexiones"],
  ["/modelos", "modelos-ia"],
  ["/consumo", "consumo"],
  ["/perfil", "perfil"],
  ["/organizacion", "organizacion"],
  ["/equipo", "equipo"],
];

describe("axe sin violaciones", () => {
  it.each(VISTAS)("%s", async (ruta, vista) => {
    await entrarDesde(ruta);
    await screen.findByTestId(`${vista}.vista.raiz`, {}, { timeout: 4000 });
    expect(await violaciones()).toEqual([]);
  });

  it.each([
    ["spec", "spec.contenido"],
    ["evidencia", "evidencia.galeria"],
    ["seguridad", "seguridad.tabla"],
    ["consumo", "consumo.tabla"],
    ["actividad", "actividad.lista"],
  ])("detalle del ticket, pestaña %s", async (zona, contenido) => {
    const usuario = await entrarDesde("/tickets/KC-1003");
    await usuario.click(await screen.findByTestId(`detalle-ticket.pestanas.${zona}`));
    await screen.findByTestId(`detalle-ticket.${contenido}`, {}, { timeout: 4000 });
    expect(await violaciones()).toEqual([]);
  });

  it.each([
    ["/login", "login"],
    ["/recuperar-clave", "recuperar-clave"],
    ["/restablecer-clave?token=abc", "restablecer-clave"],
    ["/invitacion?token=abc", "aceptar-invitacion"],
  ])("pantalla pública %s", async (ruta, vista) => {
    abrirSinSesion(ruta);
    await screen.findByTestId(`${vista}.vista.raiz`);
    expect(await violaciones()).toEqual([]);
  });

  it("con un diálogo abierto (invitar persona)", async () => {
    await entrarDesde("/equipo");
    await userEvent.click(await screen.findByTestId("equipo.cabecera.invitar"));
    await screen.findByTestId("equipo.invitar.dialogo");
    expect(await violaciones()).toEqual([]);
  });

  it("con la bandeja de notificaciones abierta", async () => {
    await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    await userEvent.click(screen.getByTestId("layout.cabecera.campana"));
    await screen.findAllByTestId("bandeja-notificaciones.lista.item");
    expect(await violaciones()).toEqual([]);
  });
});

describe("título de la pestaña del navegador (WCAG 2.4.2)", () => {
  it("nombra la vista y, en el detalle, el ticket; sigue al idioma", async () => {
    const usuario = await entrarDesde("/tickets/KC-1003");
    await screen.findByTestId("detalle-ticket.vista.raiz");
    expect(document.title).toBe("KC-1003 · Tickets · Kallicode");

    await usuario.click(screen.getByTestId("layout.nav.equipo"));
    await screen.findByTestId("equipo.vista.raiz");
    expect(document.title).toBe("Equipo · Kallicode");

    await usuario.selectOptions(screen.getByTestId("layout.cabecera.idioma"), "en");
    expect(document.title).toBe("Team · Kallicode");
  });

  it("las pantallas públicas también tienen el suyo", async () => {
    abrirSinSesion("/recuperar-clave");
    await screen.findByTestId("recuperar-clave.vista.raiz");
    expect(document.title).toBe("Recuperar contraseña · Kallicode");
  });
});
