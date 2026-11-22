/**
 * Estados de carga y vacíos de las 9 vistas de lista (KC-PT-09-4, cierra PT-F28), en el portal
 * completo: mientras carga, el título y la silueta (un solo indicador, sin vista.raiz); sin
 * datos, el estado vacío con su título y explicación.
 */
import { screen, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { kanban, notificaciones, resumenConsumo } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

const vacioPaginado = { items: [], total: 0, page: 1, page_size: 25 };
const esperar = (ruta: string) => http.get(ruta, () => delay("infinite"));

describe("mientras carga: título y silueta, con un solo indicador", () => {
  it.each([
    ["/tickets", "*/api/v1/tickets", "tickets.vista.raiz", "Tickets"],
    ["/aprobaciones", "*/api/v1/approvals/pending", "aprobaciones.vista.raiz", "Aprobaciones"],
    ["/auditoria", "*/api/v1/audit/events", "auditoria.vista.raiz", "Auditoría"],
    ["/consumo", "*/api/v1/usage/summary", "consumo.vista.raiz", "Consumo"],
    ["/modelos", "*/api/v1/models/assignments", "modelos-ia.vista.raiz", "Modelos"],
    ["/", "*/api/v1/dashboard/kanban", "tablero.vista.raiz", "Tablero"],
    ["/conexiones", "*/api/v1/connections", "conexiones.vista.raiz", "Conexiones"],
  ])("%s", async (ruta, api, raiz, titulo) => {
    server.use(esperar(api));
    await entrarDesde(ruta);

    const indicador = await screen.findByTestId("comun.carga.indicador");
    expect(screen.getAllByTestId("comun.carga.indicador")).toHaveLength(1);
    expect(indicador).toHaveAttribute("role", "status");
    expect(indicador).toHaveTextContent("Cargando…");
    // La silueta es decorativa: los lectores de pantalla solo oyen «Cargando…».
    expect(indicador.querySelector("[aria-hidden='true']")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(new RegExp(titulo, "i"));
    // Las vistas que antes mostraban solo «Cargando…» no anuncian vista.raiz hasta cargar
    // (tickets y auditoría ya la mostraban con sus filtros).
    if (!["/tickets", "/auditoria"].includes(ruta)) {
      expect(screen.queryByTestId(raiz)).not.toBeInTheDocument();
    }
  });
});

describe("sin datos: el estado vacío de cada lista", () => {
  it("tickets: sin tickets, dice que aún no hay", async () => {
    server.use(http.get("*/api/v1/tickets", () => HttpResponse.json(vacioPaginado)));
    await entrarDesde("/tickets");

    const vacio = await screen.findByTestId("tickets.lista.vacio");
    expect(vacio.tagName).toBe("TR");
    expect(vacio).toHaveTextContent("Aún no hay tickets");
    expect(within(vacio).getByRole("cell")).toHaveAttribute("colspan", "7");
  });

  it("tickets: con búsqueda, dice que nada coincide", async () => {
    server.use(http.get("*/api/v1/tickets", () => HttpResponse.json(vacioPaginado)));
    const usuario = await entrarDesde("/tickets");
    await usuario.type(await screen.findByTestId("tickets.filtros.busqueda"), "zzz");

    expect(await screen.findByText("Ningún ticket coincide con la búsqueda")).toBeInTheDocument();
    expect(screen.getByTestId("tickets.lista.vacio")).toHaveTextContent(
      "Prueba con otras palabras o quita algún filtro.",
    );
  });

  it.each([
    [
      "aprobaciones",
      "/aprobaciones",
      () => http.get("*/api/v1/approvals/pending", () => HttpResponse.json({ items: [] })),
      "aprobaciones.lista.vacio",
      "No hay gates esperando tu firma",
    ],
    [
      "consumo por ticket",
      "/consumo",
      () =>
        http.get("*/api/v1/usage/by-ticket", () =>
          HttpResponse.json({ ...vacioPaginado, page_size: 20 }),
        ),
      "consumo.tickets.vacio",
      "Ningún ticket consumió en este ciclo",
    ],
    [
      "modelos",
      "/modelos",
      () =>
        http.get("*/api/v1/models/assignments", () =>
          HttpResponse.json({ asignaciones: [], politica_escalado: "—" }),
        ),
      "modelos-ia.asignaciones.vacio",
      "Sin asignaciones de modelos",
    ],
    [
      "tablero",
      "/",
      () =>
        http.get("*/api/v1/dashboard/kanban", () =>
          HttpResponse.json({
            ...kanban,
            columnas: Object.fromEntries(Object.keys(kanban.columnas).map((c) => [c, []])),
          }),
        ),
      "tablero.kanban.vacio",
      "No hay tickets en curso",
    ],
    [
      "auditoría",
      "/auditoria",
      () =>
        http.get("*/api/v1/audit/events", () =>
          HttpResponse.json({ ...vacioPaginado, page_size: 50, cadena: { eventos_hoy: 0 } }),
        ),
      "auditoria.lista.vacio",
      "No hay eventos con estos filtros.",
    ],
  ])("%s", async (_, ruta, respuesta, testid, titulo) => {
    server.use(respuesta());
    await entrarDesde(ruta);

    const vacio = await screen.findByTestId(testid);
    expect(vacio).toHaveTextContent(titulo);
    // El ícono es decorativo.
    expect(vacio.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("actividad del ticket: la pestaña sin eventos lo dice", async () => {
    server.use(
      http.get("*/api/v1/tickets/:numero/activity", () =>
        HttpResponse.json({ items: [], page: 1, page_size: 50 }),
      ),
    );
    const usuario = await entrarDesde("/tickets/KC-1001");
    await usuario.click(await screen.findByTestId("detalle-ticket.pestanas.actividad"));

    expect(await screen.findByTestId("detalle-ticket.actividad.vacio")).toHaveTextContent(
      "Sin actividad registrada",
    );
  });

  it("notificaciones: la bandeja vacía lo dice y explica cuándo llegan", async () => {
    server.use(
      http.get("*/api/v1/notifications", () =>
        HttpResponse.json({ ...notificaciones, items: [], no_leidas: 0 }),
      ),
    );
    const usuario = await entrarDesde("/");
    await usuario.click(await screen.findByTestId("layout.cabecera.campana"));

    const vacio = await screen.findByTestId("bandeja-notificaciones.lista.vacio");
    expect(vacio).toHaveTextContent("No tienes notificaciones.");
    expect(vacio).toHaveTextContent("Te avisaremos cuando un ticket necesite tu atención.");
  });

  it("conexiones: una categoría sin conexiones lo dice en su tarjeta", async () => {
    await entrarDesde("/conexiones");

    const vacios = await screen.findAllByTestId("conexiones.lista.vacio");
    expect(vacios[0]).toHaveTextContent("Sin conexiones en esta categoría");
    expect(vacios[0]).toHaveClass("compacto");
  });

  it("con datos no aparece ningún estado vacío ni indicador de carga", async () => {
    server.use(http.get("*/api/v1/usage/summary", () => HttpResponse.json(resumenConsumo)));
    await entrarDesde("/consumo");

    await screen.findByTestId("consumo.tickets.tabla");
    expect(screen.queryByTestId("consumo.tickets.vacio")).not.toBeInTheDocument();
    expect(screen.queryByTestId("comun.carga.indicador")).not.toBeInTheDocument();
  });
});
