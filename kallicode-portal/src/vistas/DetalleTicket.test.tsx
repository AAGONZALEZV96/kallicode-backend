/** Enlace al PR del detalle de ticket: pr_url saneado (KC-PT-03-4, cierra PT-F22). */
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { detalleTicket } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

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
