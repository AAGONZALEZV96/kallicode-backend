import { fireEvent, render, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { fijarSesion } from "../api/client";
import { MAX_TEXTO_PREVIEW } from "../api/tipos";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import NuevoTicket from "./NuevoTicket";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

describe("NuevoTicket: deduplicación en vivo", () => {
  it("con una descripción larga envía como máximo el tope de texto que acepta el backend", async () => {
    let texto = "";
    server.use(
      http.post("*/api/v1/tickets/dedup-preview", async ({ request }) => {
        ({ texto } = (await request.json()) as { texto: string });
        return HttpResponse.json({ similares: [] });
      }),
    );
    const { container } = render(
      <Proveedores>
        <MemoryRouter>
          <NuevoTicket />
        </MemoryRouter>
      </Proveedores>,
    );

    // Las etiquetas aún no están asociadas a sus campos (KC-PT-05-4): se busca el textarea.
    const descripcion = container.querySelector("textarea");
    if (!descripcion) throw new Error("falta el campo de descripción");
    fireEvent.change(descripcion, {
      target: { value: "x".repeat(MAX_TEXTO_PREVIEW + 500) },
    });

    await waitFor(() => expect(texto).not.toBe(""), { timeout: 2000 });
    expect(texto).toHaveLength(MAX_TEXTO_PREVIEW);
  });
});
