/** Control de acceso por rol en el portal completo (PT-F06), con los roles del backend. */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { perfilOwner } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

/** El perfil de /users/me manda sobre el de login: así se entra con otro rol. */
function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

/** Cuenta las consultas del badge de aprobaciones (las respuestas siguen al doble general). */
function contarPendientes() {
  const cuenta = { n: 0 };
  server.use(
    http.get("*/api/v1/approvals/pending", () => {
      cuenta.n++;
    }),
  );
  return cuenta;
}

describe("RutaProtegida", () => {
  it("un viewer que abre una vista sin permiso vuelve al tablero y no ve el enlace", async () => {
    conRol("viewer");
    await entrarDesde("/conexiones");

    expect(await screen.findByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
    expect(rutaActual()).toBe("/");
    expect(screen.queryByRole("link", { name: /Conexiones/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Configuración")).not.toBeInTheDocument();
  });

  it("explica la redirección en el idioma del usuario y el aviso se va al navegar", async () => {
    localStorage.setItem("kc_idioma", "en");
    conRol("viewer");
    const usuario = await entrarDesde("/conexiones");

    expect(
      await screen.findByText(
        "Your role (Viewer) can't access that section. We took you to the dashboard.",
      ),
    ).toBeInTheDocument();

    await usuario.click(screen.getByRole("link", { name: /Tickets/ }));
    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
    expect(screen.queryByText(/Your role/)).not.toBeInTheDocument();
  });

  it("en español nombra el rol traducido", async () => {
    conRol("member");
    await entrarDesde("/auditoria");

    expect(
      await screen.findByText(
        "Tu rol (Miembro) no tiene acceso a esa sección. Te llevamos al tablero.",
      ),
    ).toBeInTheDocument();
  });

  it("una entrada directa al tablero no muestra aviso", async () => {
    await entrarDesde();
    await screen.findByRole("heading", { name: "Tablero de avance" });

    expect(screen.queryByText(/Tu rol/)).not.toBeInTheDocument();
  });

  it("las rutas con parámetro se comparan por patrón: un viewer abre /tickets/:numero", async () => {
    conRol("viewer");
    await entrarDesde("/tickets/KC-1001");

    expect(
      await screen.findByRole("heading", { name: "Timeout en búsqueda de clientes" }),
    ).toBeInTheDocument();
    expect(rutaActual()).toBe("/tickets/KC-1001");
  });

  it.each<Rol>(["architect", "approver"])(
    "un %s ve el badge de pendientes y entra a Aprobaciones",
    async (rol) => {
      conRol(rol);
      const pendientes = contarPendientes();
      const usuario = await entrarDesde();
      await screen.findByRole("heading", { name: "Tablero de avance" });
      // En el tablero, la única consulta de pendientes es la del badge.
      await waitFor(() => expect(pendientes.n).toBe(1));

      await usuario.click(screen.getByRole("link", { name: /Aprobaciones/ }));

      expect(
        await screen.findByRole("heading", { name: "Aprobaciones pendientes" }),
      ).toBeInTheDocument();
      expect(rutaActual()).toBe("/aprobaciones");
    },
  );

  it("un member no consulta aprobaciones en el polling de la barra", async () => {
    conRol("member");
    const pendientes = contarPendientes();
    await entrarDesde();
    await screen.findByRole("heading", { name: "Tablero de avance" });

    expect(screen.queryByRole("link", { name: /Aprobaciones/ })).not.toBeInTheDocument();
    expect(pendientes.n).toBe(0);
  });

  it("navegar entre vistas no reinicia el polling de la barra", async () => {
    const pendientes = contarPendientes();
    const usuario = await entrarDesde();
    await screen.findByRole("heading", { name: "Tablero de avance" });
    await waitFor(() => expect(pendientes.n).toBe(1));

    // Tickets y Tablero no consultan pendientes: cualquier consulta extra sería del badge.
    await usuario.click(screen.getByRole("link", { name: /Tickets/ }));
    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
    await usuario.click(screen.getByRole("link", { name: /Tablero/ }));
    await screen.findByRole("heading", { name: "Tablero de avance" });

    expect(pendientes.n).toBe(1);
  });
});
