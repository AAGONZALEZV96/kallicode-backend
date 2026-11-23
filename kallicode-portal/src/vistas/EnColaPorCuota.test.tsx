/**
 * Tickets en cola por cuota y almacenamiento en las vistas (KC-PT-09-5): por qué un ticket no
 * avanza, la lista filtrada por la URL, el aviso al crear un ticket con la cuota agotada y el
 * almacenamiento y la cola en Consumo.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { UsageSummary } from "../api/tipos";
import { detalleTicket, resumenConsumo } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

const GB = 1024 ** 3;
const texto = (el: HTMLElement) => (el.textContent ?? "").replace(/\s/g, " ");

function resumen(cambios: Partial<UsageSummary>) {
  server.use(
    http.get("*/api/v1/usage/summary", () => HttpResponse.json({ ...resumenConsumo, ...cambios })),
  );
}

/** Valores de `etapa` pedidos a GET /tickets (null si no se mandó). */
function etapasPedidas() {
  const etapas: (string | null)[] = [];
  server.events.on("request:start", ({ request }) => {
    const u = new URL(request.url);
    if (request.method === "GET" && u.pathname === "/api/v1/tickets")
      etapas.push(u.searchParams.get("etapa"));
  });
  return etapas;
}

describe("detalle de un ticket en cola", () => {
  function enCola(etapa = "en_cola_por_cuota") {
    server.use(
      http.get("*/api/v1/tickets/:numero", () =>
        HttpResponse.json({ ...detalleTicket("KC-1001")!, etapa }),
      ),
    );
  }

  it("dice por qué no avanza y cuándo entrará al pipeline", async () => {
    enCola();
    await entrarDesde("/tickets/KC-1001");

    const aviso = await screen.findByTestId("detalle-ticket.avisos.cola");
    expect(aviso).toHaveAttribute("role", "status");
    expect(texto(aviso)).toContain(
      "Este ticket está en cola porque se agotó la cuota de LOC del ciclo. Entrará al pipeline cuando se renueve la cuota, el 1 oct 2026.",
    );
    // No promete que mejorar el plan lo libere: solo la renovación lo hace (billing_cycle).
    expect(aviso).not.toHaveTextContent(/mejora/i);
    expect(within(aviso).getByTestId("detalle-ticket.avisos.cola-enlace")).toHaveAttribute(
      "href",
      "/consumo",
    );
  });

  it("sin el resumen de consumo, el aviso va sin fecha", async () => {
    enCola();
    server.use(http.get("*/api/v1/usage/summary", () => sobreError(500, "ERROR_INTERNO", "Falló")));
    await entrarDesde("/tickets/KC-1001");

    const aviso = await screen.findByTestId("detalle-ticket.avisos.cola");
    expect(texto(aviso)).toContain(
      "Entrará al pipeline cuando se renueve la cuota. Ver consumo y plan",
    );
    expect(aviso).not.toHaveTextContent("2026");
  });

  it.each(["triage", "cancelado"])("un ticket en %s no lo muestra", async (etapa) => {
    enCola(etapa);
    await entrarDesde("/tickets/KC-1001");

    await screen.findByTestId("detalle-ticket.vista.raiz");
    expect(screen.queryByTestId("detalle-ticket.avisos.cola")).toBeNull();
  });
});

describe("lista de tickets con la etapa en la URL", () => {
  it("?etapa=en_cola_por_cuota filtra desde el inicio", async () => {
    const etapas = etapasPedidas();
    await entrarDesde("/tickets?etapa=en_cola_por_cuota");

    await screen.findByTestId("tickets.vista.raiz");
    expect(screen.getByTestId("tickets.filtros.etapa")).toHaveValue("en_cola_por_cuota");
    await waitFor(() => expect(etapas).toContain("en_cola_por_cuota"));
    expect(etapas).not.toContain(null);
  });

  it("una etapa desconocida en la URL se ignora", async () => {
    const etapas = etapasPedidas();
    await entrarDesde("/tickets?etapa=inventada");

    await screen.findByTestId("tickets.vista.raiz");
    expect(screen.getByTestId("tickets.filtros.etapa")).toHaveValue("");
    await waitFor(() => expect(etapas.length).toBeGreaterThan(0));
    expect(etapas.every((e) => e === null)).toBe(true);
  });

  it("volver a «Todas las etapas» la quita de la URL", async () => {
    const usuario = await entrarDesde("/tickets?etapa=en_cola_por_cuota&page=2");
    await screen.findByTestId("tickets.vista.raiz");

    await usuario.selectOptions(screen.getByTestId("tickets.filtros.etapa"), "");

    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
  });
});

describe("crear un ticket con la cuota agotada", () => {
  it.each([
    [
      "es",
      "Se agotó la cuota de LOC del ciclo: el ticket quedó en cola y entrará al pipeline cuando se renueve la cuota.",
    ],
    [
      "en",
      "This cycle's LOC quota is exhausted: the ticket was queued and will enter the pipeline when the quota renews.",
    ],
  ])("en %s avisa que quedó en cola y luego abre el ticket", async (idioma, mensaje) => {
    localStorage.setItem("kc_idioma", idioma);
    server.use(
      http.post("*/api/v1/tickets", () =>
        HttpResponse.json(
          { id: "KC-1001", etapa: "en_cola_por_cuota", duplicado_potencial: null },
          { status: 202 },
        ),
      ),
    );
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.type(
      await screen.findByTestId("nuevo-ticket.formulario.titulo"),
      "Error al exportar",
    );
    await usuario.type(
      screen.getByTestId("nuevo-ticket.formulario.descripcion"),
      "El reporte mensual falla",
    );
    await usuario.click(screen.getByTestId("nuevo-ticket.formulario.enviar"));

    expect(await screen.findByTestId("nuevo-ticket.formulario.aviso")).toHaveTextContent(mensaje);
    await waitFor(() => expect(rutaActual()).toBe("/tickets/KC-1001"), { timeout: 3000 });
  });
});

describe("Consumo: almacenamiento y cola", () => {
  it("el almacenamiento con su barra, su uso y el color del umbral", async () => {
    resumen({
      almacenamiento: {
        usados_bytes: 21 * GB,
        limite_bytes: 25 * GB,
        porcentaje: 84,
        umbral: "aviso_80",
      },
    });
    await entrarDesde("/consumo");

    const barra = await screen.findByTestId("consumo.almacenamiento.barra");
    expect(barra).toHaveAttribute("role", "progressbar");
    expect(barra).toHaveAttribute("aria-valuenow", "84");
    expect(barra).toHaveAccessibleName("Almacenamiento de evidencia");
    expect(barra.firstElementChild).toHaveClass("warn");
    expect((barra.firstElementChild as HTMLElement).style.width).toBe("84%");
    expect(texto(screen.getByTestId("consumo.almacenamiento.uso"))).toBe("21 GB / 25 GB · 84 %");
  });

  it("con el almacenamiento agotado la barra es roja y no pasa del 100 %", async () => {
    resumen({
      almacenamiento: {
        usados_bytes: 26 * GB,
        limite_bytes: 25 * GB,
        porcentaje: 104,
        umbral: "agotado",
      },
    });
    await entrarDesde("/consumo");

    const barra = await screen.findByTestId("consumo.almacenamiento.barra");
    expect(barra.firstElementChild).toHaveClass("err");
    expect(barra).toHaveAttribute("aria-valuenow", "100");
    expect((barra.firstElementChild as HTMLElement).style.width).toBe("100%");
  });

  it("un plan sin límite de almacenamiento: sin barra, solo lo usado", async () => {
    resumen({
      almacenamiento: {
        usados_bytes: 3 * GB,
        limite_bytes: null,
        porcentaje: null,
        umbral: "normal",
      },
    });
    await entrarDesde("/consumo");

    expect(texto(await screen.findByTestId("consumo.almacenamiento.uso"))).toBe(
      "3 GB usados · sin límite en tu plan",
    );
    expect(screen.queryByTestId("consumo.almacenamiento.barra")).toBeNull();
  });

  it("los tickets en cola bajo la cuota de LOC, con el enlace a la lista", async () => {
    resumen({ cola: { tickets: 3, desde: "2026-09-20T10:00:00Z" } });
    const usuario = await entrarDesde("/consumo");

    const cola = await screen.findByTestId("consumo.cola.resumen");
    expect(texto(cola)).toContain(
      "Tickets en cola por cuota: 3 (el primero desde el 20 sept 2026).",
    );

    await usuario.click(within(cola).getByTestId("consumo.cola.enlace"));
    await waitFor(() => expect(rutaActual()).toBe("/tickets?etapa=en_cola_por_cuota"));
  });

  it("sin tickets en cola no hay resumen de cola", async () => {
    await entrarDesde("/consumo");

    await screen.findByTestId("consumo.almacenamiento.uso");
    expect(screen.queryByTestId("consumo.cola.resumen")).toBeNull();
  });
});
