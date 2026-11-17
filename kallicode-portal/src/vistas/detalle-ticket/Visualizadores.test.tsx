/**
 * Visualizadores del ticket (KC-PT-06-2): seguir, dejar de seguir, agregar con el buscador
 * y quitar, con las reglas de tickets.add_watcher / del_watcher por rol. En KC-1001 el
 * creador es María G. (u_dev) y quien entra es u_owner con el rol de cada prueba.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished } from "vitest";

import type { Rol, TicketDetalle } from "../../api/tipos";
import { detalleTicket, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const MARIA = { id: "u_dev", nombre: "María G." };
const ANA = { id: "u_admin", nombre: "Ana Admin" };
const YO = { id: "u_owner", nombre: "Olga Owner" };

function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

/** El detalle de KC-1001 con otros visualizadores o creador. */
function conDetalle(cambios: Partial<TicketDetalle>) {
  server.use(
    http.get("*/api/v1/tickets/KC-1001", () =>
      HttpResponse.json({ ...detalleTicket("KC-1001"), ...cambios }),
    ),
  );
}

/** Cuenta las búsquedas de usuarios (GET /users). */
function busquedas() {
  const urls: string[] = [];
  const anotar = ({ request }: { request: Request }) => {
    if (new URL(request.url).pathname === "/api/v1/users") urls.push(request.url);
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return urls;
}

const id = (elemento: string) => screen.getByTestId(`detalle-ticket.visualizadores.${elemento}`);
const quiza = (elemento: string) =>
  screen.queryByTestId(`detalle-ticket.visualizadores.${elemento}`);
/** El nombre de cada visualizador, sin el avatar ni el botón de quitar. */
const nombres = () =>
  screen
    .getAllByTestId("detalle-ticket.visualizadores.item")
    .map((li) => li.querySelector(".visualizador-nombre")?.textContent);

async function abrir() {
  const usuario = await entrarDesde("/tickets/KC-1001");
  await screen.findByTestId("detalle-ticket.visualizadores.lista");
  return usuario;
}

describe("seguir el ticket", () => {
  it("muestra al creador, y seguir / dejar de seguir usa el mismo botón (el foco se queda)", async () => {
    const usuario = await abrir();

    expect(nombres()).toEqual(["María G. · creador"]);
    // Nadie puede quitar al creador.
    expect(quiza("quitar")).not.toBeInTheDocument();

    const boton = id("seguir");
    await usuario.click(boton);

    await waitFor(() => expect(nombres()).toEqual(["María G. · creador", "Olga Owner · tú"]));
    expect(boton).toHaveTextContent("Dejar de seguir");
    expect(boton).toHaveAttribute("data-testid", "detalle-ticket.visualizadores.dejar-de-seguir");
    expect(boton).toHaveFocus();
    // Uno no se quita con el ✕: para eso está «Dejar de seguir».
    expect(screen.queryByRole("button", { name: "Quitar a Olga Owner" })).not.toBeInTheDocument();

    await usuario.click(boton);

    await waitFor(() => expect(nombres()).toEqual(["María G. · creador"]));
    expect(boton).toHaveTextContent("Seguir este ticket");
  });

  it("mientras se guarda, el botón no se deshabilita (perdería el foco) ni envía dos veces", async () => {
    let peticiones = 0;
    let responder: () => void = () => {};
    server.use(
      http.post("*/api/v1/tickets/:numero/watchers", async () => {
        peticiones++;
        await new Promise<void>((r) => (responder = r));
        return HttpResponse.json({ visualizadores: [MARIA, YO] });
      }),
    );
    const usuario = await abrir();
    const boton = id("seguir");

    await usuario.click(boton);
    await usuario.click(boton);

    expect(boton).not.toBeDisabled();
    expect(boton).toHaveAttribute("aria-disabled", "true");
    expect(boton).toHaveFocus();
    expect(peticiones).toBe(1);

    responder();
    await waitFor(() => expect(boton).toHaveTextContent("Dejar de seguir"));
    expect(boton).toHaveAttribute("aria-disabled", "false");
  });

  it("el creador no puede dejar de seguir su ticket", async () => {
    conDetalle({ reportado_por: YO, visualizadores: [YO] });
    await abrir();

    expect(quiza("dejar-de-seguir")).not.toBeInTheDocument();
    expect(quiza("seguir")).not.toBeInTheDocument();
  });
});

describe("agregar con el buscador", () => {
  it("busca personas activas, no ofrece a quienes ya siguen y agrega la elegida", async () => {
    const consultas = busquedas();
    const usuario = await abrir();

    await usuario.click(id("agregar"));

    expect(id("agregar")).toHaveAttribute("aria-expanded", "true");
    const buscador = within(id("buscador"));
    await waitFor(() =>
      expect(screen.getAllByTestId("detalle-ticket.visualizadores.resultado")).toHaveLength(4),
    );
    // María ya sigue el ticket; Iván está invitado (no activo).
    expect(buscador.queryByRole("button", { name: /María G\./ })).not.toBeInTheDocument();
    expect(buscador.queryByRole("button", { name: /Iván/ })).not.toBeInTheDocument();
    expect(new URL(consultas[0]!).searchParams.get("estado")).toBe("activo");

    await usuario.type(id("buscar"), "ana");
    await waitFor(() =>
      expect(screen.getAllByTestId("detalle-ticket.visualizadores.resultado")).toHaveLength(1),
    );
    expect(consultas.some((u) => new URL(u).searchParams.get("q") === "ana")).toBe(true);

    await usuario.click(
      buscador.getByRole("button", { name: "Agregar a Ana Admin (admin@demo.kallicode.dev)" }),
    );

    await waitFor(() => expect(nombres()).toContain("Ana Admin"));
    // Ana sale de los resultados: el foco vuelve al buscador.
    expect(id("buscar")).toHaveFocus();
    expect(id("sin-resultados")).toHaveTextContent("No hay más personas que coincidan.");
  });

  it("un member puede seguir el ticket pero no elegir a otras personas (no lista usuarios)", async () => {
    conRol("member");
    const consultas = busquedas();
    await abrir();

    expect(id("seguir")).toBeInTheDocument();
    expect(quiza("agregar")).not.toBeInTheDocument();
    expect(consultas).toEqual([]);
  });

  it("un viewer no puede seguir el ticket ni agregar personas", async () => {
    conRol("viewer");
    await abrir();
    expect(quiza("seguir")).not.toBeInTheDocument();
    expect(quiza("agregar")).not.toBeInTheDocument();
  });

  it("un viewer que ya es visualizador puede dejar de seguir", async () => {
    conRol("viewer");
    conDetalle({ visualizadores: [MARIA, YO] });
    await abrir();

    expect(id("dejar-de-seguir")).toBeInTheDocument();
  });
});

describe("quitar", () => {
  it("un admin quita a otra persona y el foco pasa a la lista", async () => {
    conRol("admin");
    conDetalle({ visualizadores: [MARIA, ANA] });
    const usuario = await abrir();

    await usuario.click(screen.getByRole("button", { name: "Quitar a Ana Admin" }));

    await waitFor(() => expect(nombres()).toEqual(["María G. · creador"]));
    expect(id("lista")).toHaveFocus();
  });

  it("el creador del ticket también puede quitar a otros", async () => {
    conRol("member");
    conDetalle({ reportado_por: YO, visualizadores: [YO, ANA] });
    await abrir();

    expect(screen.getByRole("button", { name: "Quitar a Ana Admin" })).toBeInTheDocument();
  });

  it.each<Rol>(["member", "approver", "architect"])(
    "%s sin ser el creador no puede quitar a otros",
    async (rol) => {
      conRol(rol);
      conDetalle({ visualizadores: [MARIA, ANA] });
      await abrir();

      expect(quiza("quitar")).not.toBeInTheDocument();
    },
  );
});

describe("límites y errores", () => {
  it("con 25 visualizadores no ofrece seguir ni agregar y lo explica", async () => {
    const veinticinco = Array.from({ length: 25 }, (_, i) => ({
      id: `u_${i}`,
      nombre: `Persona ${i}`,
    }));
    conDetalle({ visualizadores: veinticinco });
    await abrir();

    expect(id("limite")).toHaveTextContent("Este ticket ya tiene el máximo de 25 visualizadores.");
    expect(quiza("seguir")).not.toBeInTheDocument();
    expect(quiza("agregar")).not.toBeInTheDocument();
  });

  it("si el backend rechaza el cambio, lo dice en la tarjeta y la lista no cambia", async () => {
    server.use(
      http.post("*/api/v1/tickets/:numero/watchers", () =>
        sobreError(409, "LIMITE_VISUALIZADORES", "Máximo 25 visualizadores por ticket."),
      ),
    );
    const usuario = await abrir();

    await usuario.click(id("seguir"));

    expect(await screen.findByTestId("comun.error.caja")).toHaveTextContent(
      "LIMITE_VISUALIZADORES",
    );
    expect(nombres()).toEqual(["María G. · creador"]);
  });
});
