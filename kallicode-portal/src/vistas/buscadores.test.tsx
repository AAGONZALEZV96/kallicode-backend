/**
 * Buscadores (Tickets, Auditoría) y previsualizaciones en vivo (duplicados, impacto):
 * debounce y cancelación (KC-PT-04-3, cierra PT-F08).
 */
import { screen, waitFor } from "@testing-library/react";
import { http } from "msw";
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { api, type OpcionesPeticion } from "../api/client";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

/** Cuántas veces se pidió `ruta` (método POST) durante la prueba. */
function contarPost(ruta: string) {
  const cuenta = { n: 0 };
  const anotar = ({ request }: { request: Request }) => {
    if (request.method === "POST" && new URL(request.url).pathname === ruta) cuenta.n++;
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return cuenta;
}

describe.each([
  ["duplicados de Nuevo ticket", "/nuevo-ticket", "/api/v1/tickets/dedup-preview"],
  ["impacto de Nueva funcionalidad", "/nueva-funcionalidad", "/api/v1/tickets/impact-preview"],
])("previsualización de %s", (_nombre, rutaPortal, rutaApi) => {
  it("pide una sola vez al dejar de escribir, no con cada tecla", async () => {
    const pedidas = contarPost(rutaApi);
    const usuario = await entrarDesde(rutaPortal);
    const campo = await waitFor(() => {
      const el = document.querySelector("textarea");
      if (!el) throw new Error("aún no está el formulario");
      return el;
    });

    await usuario.type(campo, "error al exportar facturas del mes");

    await waitFor(() => expect(pedidas.n).toBe(1));
    await new Promise((r) => setTimeout(r, 500));
    expect(pedidas.n).toBe(1);
  });
});

/** Valores de `q` con que se pidió `ruta`, en orden. */
function busquedasEnviadas(ruta: string) {
  const enviadas: string[] = [];
  const anotar = ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    if (url.pathname === ruta) enviadas.push(url.searchParams.get("q") ?? "");
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return enviadas;
}

describe.each([
  ["Tickets", "/tickets", "/api/v1/tickets", "KC-1001"],
  ["Auditoría", "/auditoria", "/api/v1/audit/events", "Auditoría"],
])("buscador de %s", (_vista, rutaPortal, rutaApi, visible) => {
  async function abrir() {
    const usuario = await entrarDesde(rutaPortal);
    await screen.findAllByText(visible);
    return usuario;
  }

  it("busca al dejar de escribir, no con cada tecla", async () => {
    const enviadas = busquedasEnviadas(rutaApi);
    const usuario = await abrir();

    // El espacio final no cambia la búsqueda: no genera otra petición.
    await usuario.type(screen.getByRole("searchbox"), "factura ");

    await waitFor(() => expect(enviadas).toContain("factura"));
    expect(enviadas.filter((q) => q !== "")).toEqual(["factura"]);
  });

  it("si la búsqueda cambia con una petición en curso, cancela la anterior", async () => {
    const usuario = await abrir();
    // La búsqueda «fac» se queda colgada en el servidor.
    server.use(
      http.get(`*${rutaApi}`, ({ request }) =>
        new URL(request.url).searchParams.get("q") === "fac"
          ? new Promise<never>(() => {})
          : undefined,
      ),
    );
    const senales = new Map<string, AbortSignal | undefined>();
    const original = api.get;
    const espia = vi
      .spyOn(api, "get")
      .mockImplementation(<T,>(ruta: string, opciones?: OpcionesPeticion<T>) => {
        const q = new URL(ruta, "http://x").searchParams.get("q");
        if (q) senales.set(q, opciones?.signal);
        return original<T>(ruta, opciones);
      });
    onTestFinished(() => espia.mockRestore());

    await usuario.type(screen.getByRole("searchbox"), "fac");
    await waitFor(() => expect(senales.has("fac")).toBe(true));
    await usuario.type(screen.getByRole("searchbox"), "t");
    await waitFor(() => expect(senales.has("fact")).toBe(true));

    expect(senales.get("fac")?.aborted).toBe(true);
    expect(senales.get("fact")?.aborted).toBe(false);
  });
});
