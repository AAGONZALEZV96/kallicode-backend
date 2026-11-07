/**
 * Bandeja de notificaciones (KC-PT-06-1): desplegable desde la campana, lectura individual
 * y marcado masivo, contra un backend de notificaciones con estado.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { notificaciones } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

/** Backend de notificaciones con estado: lo que marca una petición lo ve la siguiente. */
function backendNotificaciones(items = notificaciones.items) {
  const estado = items.map((n) => ({ ...n }));
  const marcadas: { id: string; leida: boolean }[] = [];
  const pedidas = { bandeja: 0, leerTodas: 0 };
  const noLeidas = () => estado.filter((n) => !n.leida).length;
  server.use(
    http.get("*/api/v1/notifications", ({ request }) => {
      if (new URL(request.url).searchParams.get("page_size") !== "1") pedidas.bandeja++;
      return HttpResponse.json({ items: estado, no_leidas: noLeidas(), page: 1, page_size: 20 });
    }),
    http.patch("*/api/v1/notifications/:id", async ({ params, request }) => {
      const { leida } = (await request.json()) as { leida: boolean };
      const n = estado.find((x) => x.id === params.id);
      if (!n) return sobreError(404, "NO_ENCONTRADO", "La notificación no existe.");
      n.leida = leida;
      marcadas.push({ id: n.id, leida });
      return HttpResponse.json({ id: n.id, leida });
    }),
    http.post("*/api/v1/notifications/read-all", () => {
      pedidas.leerTodas++;
      const n = noLeidas();
      for (const x of estado) x.leida = true;
      return HttpResponse.json({ marcadas: n });
    }),
  );
  return { marcadas, pedidas };
}

const campana = () => screen.getByTestId("layout.cabecera.campana");
const bandeja = () => screen.getByRole("region", { name: "Notificaciones" });

async function abrirBandeja() {
  const usuario = await entrarDesde("/");
  await waitFor(() => expect(campana()).toHaveAccessibleName("1 notificaciones sin leer"));
  await usuario.click(campana());
  await within(bandeja()).findAllByTestId("bandeja-notificaciones.lista.item");
  return usuario;
}

let backend: ReturnType<typeof backendNotificaciones>;
beforeEach(() => {
  backend = backendNotificaciones();
});

describe("campana", () => {
  it("muestra cuántas hay sin leer y solo consulta la bandeja al abrirla", async () => {
    const usuario = await entrarDesde("/");

    await waitFor(() =>
      expect(screen.getByTestId("layout.cabecera.notificaciones")).toHaveTextContent("1"),
    );
    expect(campana()).toHaveAttribute("aria-expanded", "false");
    expect(backend.pedidas.bandeja).toBe(0);

    await usuario.click(campana());

    expect(campana()).toHaveAttribute("aria-expanded", "true");
    expect(campana()).toHaveAttribute("aria-controls", bandeja().id);
    await waitFor(() => expect(backend.pedidas.bandeja).toBe(1));
  });

  it("lista las notificaciones: la no leída lleva «Nueva» y solo enlaza la que trae ticket_numero", async () => {
    await abrirBandeja();
    const [gate, cuota] = within(bandeja()).getAllByTestId("bandeja-notificaciones.lista.item");

    expect(gate).toHaveClass("no-leida");
    expect(within(gate!).getByText("Nueva")).toBeInTheDocument();
    expect(within(gate!).getByRole("link", { name: /GATE 1/ })).toHaveAttribute(
      "href",
      "/tickets/KC-1003",
    );
    expect(cuota).not.toHaveClass("no-leida");
    expect(within(cuota!).queryByRole("link")).not.toBeInTheDocument();
  });

  it("sin notificaciones muestra el estado vacío y no deja marcar todas", async () => {
    backendNotificaciones([]);
    const usuario = await entrarDesde("/");

    await usuario.click(await screen.findByTestId("layout.cabecera.campana"));

    expect(await within(bandeja()).findByText("No tienes notificaciones.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar todas como leídas" })).toBeDisabled();
    expect(screen.queryByTestId("layout.cabecera.notificaciones")).not.toBeInTheDocument();
  });

  it("un error del backend se muestra dentro de la bandeja", async () => {
    server.use(
      http.get("*/api/v1/notifications", ({ request }) =>
        new URL(request.url).searchParams.get("page_size") === "1"
          ? HttpResponse.json({ items: [], no_leidas: 0, page: 1, page_size: 1 })
          : sobreError(500, "ERROR_INTERNO", "Error interno."),
      ),
    );
    const usuario = await entrarDesde("/");

    await usuario.click(await screen.findByTestId("layout.cabecera.campana"));

    expect(await within(bandeja()).findByRole("alert")).toHaveTextContent("ERROR_INTERNO");
  });
});

describe("lectura", () => {
  it("abrir una notificación la marca como leída, va al ticket y cierra la bandeja", async () => {
    const usuario = await abrirBandeja();

    await usuario.click(within(bandeja()).getByRole("link", { name: /GATE 1/ }));

    await waitFor(() => expect(rutaActual()).toBe("/tickets/KC-1003"));
    expect(screen.queryByRole("region", { name: "Notificaciones" })).not.toBeInTheDocument();
    await waitFor(() => expect(backend.marcadas).toEqual([{ id: "nt_1", leida: true }]));
    await waitFor(() =>
      expect(screen.queryByTestId("layout.cabecera.notificaciones")).not.toBeInTheDocument(),
    );
  });

  it("cada notificación se marca como leída o no leída por separado", async () => {
    const usuario = await abrirBandeja();

    await usuario.click(
      screen.getByRole("button", {
        name: "Marcar «GATE 1 · Facturación por convenio corporativo» como leída",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("layout.cabecera.notificaciones")).not.toBeInTheDocument(),
    );
    await usuario.click(
      await screen.findByRole("button", {
        name: "Marcar «Llevas el 80 % de la cuota mensual» como no leída",
      }),
    );

    expect(backend.marcadas).toEqual([
      { id: "nt_1", leida: true },
      { id: "nt_2", leida: false },
    ]);
    await waitFor(() =>
      expect(screen.getByTestId("layout.cabecera.notificaciones")).toHaveTextContent("1"),
    );
  });

  it("«Marcar todas como leídas» deja el contador en cero", async () => {
    const usuario = await abrirBandeja();

    await usuario.click(screen.getByRole("button", { name: "Marcar todas como leídas" }));

    await waitFor(() =>
      expect(screen.queryByTestId("layout.cabecera.notificaciones")).not.toBeInTheDocument(),
    );
    expect(backend.pedidas.leerTodas).toBe(1);
    expect(campana()).toHaveAccessibleName("Notificaciones");
    expect(screen.getByRole("button", { name: "Marcar todas como leídas" })).toBeDisabled();
    expect(within(bandeja()).queryByText("Nueva")).not.toBeInTheDocument();
  });
});

describe("cierre", () => {
  it("Escape cierra la bandeja y devuelve el foco a la campana", async () => {
    const usuario = await abrirBandeja();
    within(bandeja()).getByRole("button", { name: "Marcar todas como leídas" }).focus();

    await usuario.keyboard("{Escape}");

    expect(screen.queryByRole("region", { name: "Notificaciones" })).not.toBeInTheDocument();
    expect(campana()).toHaveFocus();
    expect(campana()).toHaveAttribute("aria-expanded", "false");
  });

  it("un clic afuera la cierra; un clic dentro no", async () => {
    const usuario = await abrirBandeja();

    await usuario.click(within(bandeja()).getByRole("heading", { name: "Notificaciones" }));
    expect(bandeja()).toBeInTheDocument();

    await usuario.click(screen.getByTestId("tablero.vista.raiz"));
    expect(screen.queryByRole("region", { name: "Notificaciones" })).not.toBeInTheDocument();
  });

  it("la campana abre y cierra", async () => {
    const usuario = await abrirBandeja();

    await usuario.click(campana());

    expect(screen.queryByRole("region", { name: "Notificaciones" })).not.toBeInTheDocument();
  });
});
