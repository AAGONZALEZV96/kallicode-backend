/** Paginación de los listados sincronizada con la URL y con el page_size real (KC-PT-04-5). */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished } from "vitest";

import { actividad, eventosAuditoria, tickets } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

/** Páginas pedidas a `ruta` (parámetro `page`, 1 si no va). */
function paginasPedidas(ruta: string) {
  const pedidas: number[] = [];
  const anotar = ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    if (url.pathname === ruta) pedidas.push(Number(url.searchParams.get("page") ?? "1"));
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return pedidas;
}

/**
 * Listado de 30 tickets en páginas de 10: el backend responde con un page_size distinto
 * del pedido (25), como puede hacer el real.
 */
function treintaTickets() {
  server.use(
    http.get("*/api/v1/tickets", ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get("page") ?? "1");
      const items = Array.from({ length: 10 }, (_, i) => ({
        ...tickets[0],
        id: `KC-${2000 + (page - 1) * 10 + i}`,
      }));
      return HttpResponse.json({ items, total: 30, page, page_size: 10 });
    }),
  );
}

const estado = (prefijo: string) => screen.findByTestId(`${prefijo}.paginacion.estado`);

describe("paginación de Tickets", () => {
  it("abre en la página de la URL y cuenta las páginas con el page_size de la respuesta", async () => {
    treintaTickets();
    const pedidas = paginasPedidas("/api/v1/tickets");

    await entrarDesde("/tickets?page=2");

    expect(await screen.findByText("KC-2010")).toBeInTheDocument();
    expect(await estado("tickets")).toHaveTextContent("Página 2 de 3");
    expect(pedidas).toContain(2);
  });

  it("avanzar deja la página en la URL", async () => {
    treintaTickets();
    const usuario = await entrarDesde("/tickets");
    await screen.findByText("KC-2000");

    await usuario.click(screen.getByRole("button", { name: "Página siguiente" }));

    expect(await screen.findByText("KC-2010")).toBeInTheDocument();
    expect(rutaActual()).toBe("/tickets?page=2");
  });

  it("volver a la primera página limpia la URL", async () => {
    treintaTickets();
    const usuario = await entrarDesde("/tickets?page=2");
    await screen.findByText("KC-2010");

    await usuario.click(screen.getByRole("button", { name: "Página anterior" }));

    expect(await screen.findByText("KC-2000")).toBeInTheDocument();
    expect(rutaActual()).toBe("/tickets");
  });

  it("cambiar un filtro vuelve a la primera página", async () => {
    treintaTickets();
    const usuario = await entrarDesde("/tickets?page=3");
    await screen.findByText("KC-2020");

    await usuario.selectOptions(screen.getByDisplayValue("Todas las etapas"), "design");

    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
  });

  it("buscar vuelve a la primera página", async () => {
    treintaTickets();
    const usuario = await entrarDesde("/tickets?page=3");
    await screen.findByText("KC-2020");

    await usuario.type(screen.getByRole("searchbox"), "x");

    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
  });

  it.each(["abc", "-3", "2.5", "0"])("?page=%s equivale a la primera página", async (valor) => {
    treintaTickets();
    const pedidas = paginasPedidas("/api/v1/tickets");

    await entrarDesde(`/tickets?page=${valor}`);

    expect(await screen.findByText("KC-2000")).toBeInTheDocument();
    expect(pedidas.every((p) => p === 1)).toBe(true);
  });
});

describe("paginación de Auditoría", () => {
  it("usa el page_size de la respuesta en vez del 50 fijo", async () => {
    server.use(
      http.get("*/api/v1/audit/events", () =>
        HttpResponse.json({ ...eventosAuditoria, total: 45, page_size: 20 }),
      ),
    );

    await entrarDesde("/auditoria");

    expect(await estado("auditoria")).toHaveTextContent("Página 1 de 3");
  });
});

describe("paginación de Consumo por ticket", () => {
  it("pide la página de la URL", async () => {
    const pedidas = paginasPedidas("/api/v1/usage/by-ticket");

    await entrarDesde("/consumo?page=2");

    await waitFor(() => expect(pedidas).toContain(2));
  });
});

describe("paginación de la actividad del ticket (sin total)", () => {
  it("usa su propio parámetro y ofrece seguir mientras la página venga llena", async () => {
    const llena = Array.from({ length: 50 }, (_, i) => ({
      ...actividad.items[0]!,
      evento: `e${i}`,
    }));
    server.use(
      http.get("*/api/v1/tickets/:numero/activity", ({ request }) => {
        const page = Number(new URL(request.url).searchParams.get("page") ?? "1");
        return HttpResponse.json({ items: page === 1 ? llena : [], page, page_size: 50 });
      }),
    );
    const pedidas = paginasPedidas("/api/v1/tickets/KC-1001/activity");
    const usuario = await entrarDesde("/tickets/KC-1001");
    await usuario.click(await screen.findByRole("button", { name: "Actividad" }));

    const paginacion = await screen.findByTestId("detalle-ticket.paginacion.contenedor");
    expect(within(paginacion).getByText("Página 1")).toBeInTheDocument();
    await usuario.click(within(paginacion).getByRole("button", { name: "Página siguiente" }));

    await waitFor(() => expect(rutaActual()).toBe("/tickets/KC-1001?actividad=2"));
    await waitFor(() => expect(pedidas).toContain(2));
  });
});
