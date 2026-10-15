/** Capa de datos con TanStack Query (KC-PT-04-1, cierra PT-F10). */
import { renderHook, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { detalleTicket, organizacion } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, Proveedores } from "../test/portal";
import { api } from "./client";
import {
  POLLING_MAXIMO_MS,
  POLLING_MINIMO_MS,
  POLLING_POR_DEFECTO,
  useIntervalosPolling,
  useTickets,
} from "./consultas";

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

describe("intervalos de polling (KC-PT-04-4)", () => {
  function conPolling(polling: object) {
    server.use(
      http.get("*/api/v1/organization", () => HttpResponse.json({ ...organizacion, polling })),
    );
    return renderHook(() => useIntervalosPolling(), { wrapper: Proveedores });
  }

  it("mientras GET /organization no responde usa los de D12: 30 s y 60 s", () => {
    const { result } = conPolling({ notificaciones_s: 10, tablero_s: 20 });

    expect(result.current).toEqual({ notificacionesMs: 30_000, tableroMs: 60_000 });
  });

  it("usa los segundos que publica el backend", async () => {
    const { result } = conPolling({ notificaciones_s: 10, tablero_s: 20 });

    await waitFor(() =>
      expect(result.current).toEqual({ notificacionesMs: 10_000, tableroMs: 20_000 }),
    );
  });

  it("descarta valores inválidos y no baja del mínimo", async () => {
    const { result } = conPolling({ notificaciones_s: 1, tablero_s: 0 });

    await waitFor(() =>
      expect(result.current).toEqual({
        notificacionesMs: POLLING_MINIMO_MS,
        tableroMs: POLLING_POR_DEFECTO.tableroMs,
      }),
    );
  });

  it("los badges del menú se refrescan al intervalo de notificaciones publicado", async () => {
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    });
    onTestFinished(() => {
      vi.useRealTimers();
    });
    server.use(
      http.get("*/api/v1/organization", () =>
        HttpResponse.json({ ...organizacion, polling: { notificaciones_s: 10, tablero_s: 60 } }),
      ),
    );
    const veces = registrarPeticiones();
    await entrarDesde("/tickets", { advanceTimers: vi.advanceTimersByTime });
    await screen.findByText("KC-1001");
    await vi.waitFor(() => expect(veces("GET /api/v1/organization")).toBe(1));
    const notificaciones = veces("GET /api/v1/notifications");
    const aprobaciones = veces("GET /api/v1/approvals/pending");

    await vi.advanceTimersByTimeAsync(10_000);

    await vi.waitFor(() => expect(veces("GET /api/v1/notifications")).toBe(notificaciones + 1));
    expect(veces("GET /api/v1/approvals/pending")).toBe(aprobaciones + 1);
  });

  it("un valor enorme queda en el máximo: no desborda setInterval", async () => {
    const { result } = conPolling({ notificaciones_s: 12, tablero_s: 3_000_000 });

    await waitFor(() => expect(result.current.tableroMs).toBe(POLLING_MAXIMO_MS));
  });

  it("un número que JSON lee como Infinity se descarta", async () => {
    server.use(
      http.get(
        "*/api/v1/organization",
        () =>
          new HttpResponse(
            JSON.stringify({ ...organizacion, polling: null }).replace(
              '"polling":null',
              '"polling":{"notificaciones_s":12,"tablero_s":1e400}',
            ),
            { headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    const { result } = renderHook(() => useIntervalosPolling(), { wrapper: Proveedores });

    await waitFor(() => expect(result.current.notificacionesMs).toBe(12_000));
    expect(result.current.tableroMs).toBe(POLLING_POR_DEFECTO.tableroMs);
  });

  it.each([-5, "60", null])("ignora tablero_s = %s", async (tablero_s) => {
    const { result } = conPolling({ notificaciones_s: 12, tablero_s });

    await waitFor(() => expect(result.current.notificacionesMs).toBe(12_000));
    expect(result.current.tableroMs).toBe(POLLING_POR_DEFECTO.tableroMs);
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
