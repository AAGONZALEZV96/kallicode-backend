/** Controles de las vistas según el rol, vía usePermiso (KC-PT-03-2, PT-F06). */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { detalleTicket, organizacion, perfilOwner } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

/** KC-1001 con el gate y el creador indicados. */
function ticket({ gate = null, creador = "u_dev" }: { gate?: number | null; creador?: string }) {
  server.use(
    http.get("*/api/v1/tickets/:numero", () => {
      const tk = detalleTicket("KC-1001");
      return tk
        ? HttpResponse.json({
            ...tk,
            gate_pendiente: gate,
            reportado_por: { id: creador, nombre: "Creador" },
          })
        : undefined;
    }),
  );
}

async function abrirTicket() {
  await entrarDesde("/tickets/KC-1001");
  await screen.findByRole("heading", { name: "Timeout en búsqueda de clientes" });
}

const botonCancelar = () => screen.queryByRole("button", { name: "Cancelar ticket" });

describe("DetalleTicket: cancelar", () => {
  it.each<[Rol, string, boolean]>([
    ["viewer", "u_dev", false],
    ["member", "u_dev", false],
    ["member", perfilOwner.id, true],
    ["admin", "u_dev", true],
    ["owner", "u_dev", true],
  ])("un %s ante un ticket de %s: botón visible = %s", async (rol, creador, visible) => {
    conRol(rol);
    ticket({ creador });
    await abrirTicket();

    if (visible) expect(botonCancelar()).toBeInTheDocument();
    else expect(botonCancelar()).not.toBeInTheDocument();
  });
});

describe("DetalleTicket: firmar gate", () => {
  it.each<[Rol, number, boolean]>([
    ["approver", 1, true],
    ["approver", 3, false],
    ["architect", 3, true],
    ["member", 1, false],
  ])("un %s ante el Gate %s: panel visible = %s", async (rol, gate, visible) => {
    conRol(rol);
    ticket({ gate });
    await abrirTicket();

    const panel = screen.queryByRole("heading", { name: new RegExp(`^GATE ${gate}`) });
    if (visible) expect(panel).toBeInTheDocument();
    else expect(panel).not.toBeInTheDocument();
  });
});

describe("Conexiones", () => {
  it.each<[Rol, boolean]>([
    ["owner", true],
    ["admin", false],
  ])("un %s ve «Encender» = %s (solo owner en /onboarding/complete)", async (rol, visible) => {
    conRol(rol);
    server.use(
      http.get("*/api/v1/organization", () =>
        HttpResponse.json({ ...organizacion, fabrica_activa: false }),
      ),
    );
    await entrarDesde("/conexiones");
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Test" }).length).toBeGreaterThan(0),
    );

    const encender = screen.queryByRole("button", { name: /Encender la primera línea/ });
    if (visible) expect(encender).toBeInTheDocument();
    else expect(encender).not.toBeInTheDocument();
  });
});

describe("Consumo: solicitar upgrade", () => {
  it.each<[Rol, boolean]>([
    ["owner", true],
    ["admin", true],
    ["architect", false],
    ["member", false],
  ])("un %s ve el botón = %s", async (rol, visible) => {
    conRol(rol);
    await entrarDesde("/consumo");
    await screen.findByRole("heading", { name: "Consumo y plan" });

    const boton = screen.queryByRole("button", { name: /Mejorar plan/ });
    if (visible) expect(boton).toBeInTheDocument();
    else expect(boton).not.toBeInTheDocument();
  });
});
