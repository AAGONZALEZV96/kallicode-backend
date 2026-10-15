/** Capa de datos con TanStack Query (KC-PT-04-1, cierra PT-F10). */
import { renderHook, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { detalleTicket } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, Proveedores } from "../test/portal";
import { api } from "./client";
import { useTickets } from "./consultas";

/** Rutas de la API pedidas durante la prueba, en orden. */
function registrarPeticiones() {
  const rutas: string[] = [];
  const anotar = ({ request }: { request: Request }) => {
    rutas.push(`${request.method} ${new URL(request.url).pathname}`);
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return (ruta: string) => rutas.filter((r) => r === ruta).length;
}

describe("caché compartida", () => {
  it("el badge del menú y la vista Aprobaciones comparten una sola petición", async () => {
    const veces = registrarPeticiones();

    await entrarDesde("/aprobaciones");
    await screen.findByRole("heading", { name: "Aprobaciones pendientes" });

    expect(veces("GET /api/v1/approvals/pending")).toBe(1);
  });

  it("al cerrar sesión se descarta la caché: al volver a entrar los datos se piden de nuevo", async () => {
    const veces = registrarPeticiones();
    const usuario = await entrarDesde("/tickets");
    await screen.findByText("KC-1001");
    expect(veces("GET /api/v1/tickets")).toBe(1);

    await usuario.click(screen.getByRole("button", { name: "Salir" }));
    const correo = await waitFor(() => {
      const el = document.querySelector<HTMLInputElement>('input[type="email"]');
      if (!el) throw new Error("aún no está el login");
      return el;
    });
    const clave = document.querySelector<HTMLInputElement>('input[type="password"]');
    if (!clave) throw new Error("falta el campo de contraseña");
    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo{Enter}");

    await screen.findByText("KC-1001");
    expect(veces("GET /api/v1/tickets")).toBe(2);
  });
});

describe("invalidación tras una mutación", () => {
  it("firmar un gate actualiza al instante el ticket y el badge de aprobaciones", async () => {
    const usuario = await entrarDesde("/tickets/KC-1003");
    const aprobar = await screen.findByRole("button", { name: "✓ Aprobar" });
    const enlace = screen.getByRole("link", { name: /Aprobaciones/ });
    await waitFor(() => expect(within(enlace).getByText("1")).toBeInTheDocument());

    // Tras la firma el backend ya no tiene el Gate 1 pendiente.
    server.use(
      http.get("*/api/v1/approvals/pending", () => HttpResponse.json({ items: [] })),
      http.get("*/api/v1/tickets/:numero", () =>
        HttpResponse.json({ ...detalleTicket("KC-1003"), gate_pendiente: null }),
      ),
    );
    await usuario.click(aprobar);

    // Sin esperar el polling de 30 s: la mutación invalida ticket y aprobaciones juntos.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "✓ Aprobar" })).not.toBeInTheDocument(),
    );
    expect(within(enlace).queryByText("1")).not.toBeInTheDocument();
  });
});

describe("filtros", () => {
  it("al cambiar de filtro se sigue viendo la tabla anterior hasta tener la nueva", async () => {
    const usuario = await entrarDesde("/tickets");
    await screen.findByText("KC-1001");
    // La búsqueda nueva no responde todavía.
    server.use(http.get("*/api/v1/tickets", () => new Promise<never>(() => {})));

    await usuario.type(screen.getByRole("searchbox"), "x");

    expect(screen.getByText("KC-1001")).toBeInTheDocument();
    expect(screen.queryByText("Cargando…")).not.toBeInTheDocument();
  });
});

describe("cancelación", () => {
  it("cambiar de filtro cancela la petición del filtro anterior", async () => {
    const senales: AbortSignal[] = [];
    const espia = vi.spyOn(api, "get").mockImplementation((_ruta, opciones) => {
      if (opciones?.signal) senales.push(opciones.signal);
      return new Promise<never>(() => {});
    });
    const filtros = (q: string) => ({ q, etapa: "", prioridad: "", page: 1 });
    const { rerender } = renderHook(({ q }) => useTickets(filtros(q)), {
      wrapper: Proveedores,
      initialProps: { q: "fac" },
    });
    await waitFor(() => expect(senales).toHaveLength(1));

    rerender({ q: "fact" });

    await waitFor(() => expect(senales).toHaveLength(2));
    expect(senales[0].aborted).toBe(true);
    expect(senales[1].aborted).toBe(false);
    espia.mockRestore();
  });
});
