/** Detalle de ticket: pr_url saneado (KC-PT-03-4) y refresco automático (KC-PT-04-4). */
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REFRESCO_TICKET_MS } from "../api/consultas";
import { detalleTicket } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

describe("DetalleTicket: refresco automático", () => {
  beforeEach(() =>
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    }),
  );
  afterEach(() => vi.useRealTimers());

  it("cada 20 s trae el estado nuevo del ticket abierto, sin recargar", async () => {
    await entrarDesde("/tickets/KC-1003", { advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "✓ Aprobar" });

    // Otro usuario firmó el Gate 1 mientras la página estaba abierta.
    server.use(
      http.get("*/api/v1/tickets/:numero", () =>
        HttpResponse.json({ ...detalleTicket("KC-1003"), gate_pendiente: null }),
      ),
    );
    await vi.advanceTimersByTimeAsync(REFRESCO_TICKET_MS - 1_000);
    expect(screen.getByRole("button", { name: "✓ Aprobar" })).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() =>
      expect(screen.queryByRole("button", { name: "✓ Aprobar" })).not.toBeInTheDocument(),
    );
  });

  it("la pestaña visible también se refresca: la spec aparece cuando el pipeline la genera", async () => {
    server.use(
      http.get("*/api/v1/tickets/:numero/spec", () =>
        sobreError(404, "SPEC_NO_DISPONIBLE", "Este ticket aún no tiene spec."),
      ),
    );
    await entrarDesde("/tickets/KC-1001", { advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("heading", { name: "Timeout en búsqueda de clientes" });
    await vi.waitFor(() => expect(screen.queryByText("Cargando…")).not.toBeInTheDocument());
    expect(screen.queryByText(/^Spec v\d/)).not.toBeInTheDocument();

    server.resetHandlers(); // el pipeline generó la spec
    await vi.advanceTimersByTimeAsync(REFRESCO_TICKET_MS);

    expect(await screen.findByText(/^Spec v\d/)).toBeInTheDocument();
  });
});

function conPrUrl(pr_url: string) {
  server.use(
    http.get("*/api/v1/tickets/:numero", () => {
      const tk = detalleTicket("KC-1001");
      return tk ? HttpResponse.json({ ...tk, detalle: { ...tk.detalle, pr_url } }) : undefined;
    }),
  );
}

async function filaPr() {
  await entrarDesde("/tickets/KC-1001");
  await screen.findByRole("heading", { name: "Timeout en búsqueda de clientes" });
  return screen.getByRole("row", { name: /^PR/ });
}

describe("DetalleTicket: enlace al PR", () => {
  it("enlaza un pr_url https", async () => {
    conPrUrl("https://github.com/demo/app/pull/479");

    const fila = await filaPr();

    expect(within(fila).getByRole("link")).toHaveAttribute(
      "href",
      "https://github.com/demo/app/pull/479",
    );
  });

  it.each(["javascript:alert(document.cookie)", "data:text/html,<script>alert(1)</script>"])(
    "no enlaza %s",
    async (pr_url) => {
      conPrUrl(pr_url);

      const fila = await filaPr();

      expect(within(fila).queryByRole("link")).not.toBeInTheDocument();
      expect(within(fila).getByText("—")).toBeInTheDocument();
    },
  );
});
