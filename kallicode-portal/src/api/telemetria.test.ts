/**
 * Telemetría del portal (KC-PT-11-5): errores agrupados por firma y Web Vitals, enviados en
 * lotes a POST /api/v1/telemetry/client.
 */
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { server } from "../mocks/server";
import { api, fijarSesion } from "./client";
import {
  ESPERA_ENVIO_MS,
  enviarPendientes,
  firmaDe,
  iniciarTelemetria,
  registrarError,
  registrarVital,
  reportarErrorCliente,
} from "./telemetria";

interface Lote {
  errores: { firma: string; tipo: string; mensaje: string; ruta: string; veces: number }[];
  vitals: { nombre: string }[];
}

/** Lotes que llegan al backend, con la cabecera Authorization de cada uno. */
function capturar() {
  const lotes: { cuerpo: Lote; autorizacion: string | null }[] = [];
  server.use(
    http.post("*/api/v1/telemetry/client", async ({ request }) => {
      lotes.push({
        cuerpo: (await request.json()) as Lote,
        autorizacion: request.headers.get("Authorization"),
      });
      return HttpResponse.json({ errores: 0, vitals: 0 }, { status: 202 });
    }),
  );
  return lotes;
}

const esperarEnvios = (lotes: unknown[], n: number) =>
  vi.waitFor(() => expect(lotes).toHaveLength(n));

beforeEach(() => window.history.pushState({}, "", "/tickets/KC-1003"));
afterEach(() => {
  vi.useRealTimers();
  window.history.pushState({}, "", "/");
});

describe("firma de un error", () => {
  it("el mismo error con otros números o identificadores es el mismo grupo", () => {
    expect(firmaDe("error", "No se encontró el ticket 1003")).toBe(
      firmaDe("error", "No se encontró el ticket 2041"),
    );
    expect(firmaDe("rechazo", "Fallo en 6f1c2a9b4e")).toBe(
      firmaDe("rechazo", "Fallo en 0a9b8c7d6e"),
    );
  });

  it("otro tipo, otro mensaje u otro origen son otro grupo", () => {
    const base = firmaDe("error", "x is undefined", "Tablero-abc.js:10");
    expect(firmaDe("rechazo", "x is undefined", "Tablero-abc.js:10")).not.toBe(base);
    expect(firmaDe("error", "y is undefined", "Tablero-abc.js:10")).not.toBe(base);
    expect(firmaDe("error", "x is undefined", "Consumo-def.js:3")).not.toBe(base);
    expect(base).toMatch(/^error-[0-9a-f]{8}$/);
  });
});

describe("envío agrupado", () => {
  it("junta los errores iguales y los manda una vez, a los 5 s, con la ruta sin el número", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const lotes = capturar();
    for (let i = 0; i < 4; i++) registrarError("error", `Falló la fila ${i}`);
    registrarError("rechazo", "Network error");
    expect(lotes).toHaveLength(0);

    vi.advanceTimersByTime(ESPERA_ENVIO_MS);
    vi.useRealTimers();
    await esperarEnvios(lotes, 1);
    const { errores } = lotes[0]!.cuerpo;
    expect(errores.map((e) => [e.tipo, e.veces])).toEqual([
      ["error", 4],
      ["rechazo", 1],
    ]);
    expect(errores[0]!.ruta).toBe("/tickets/:numero");
    expect(errores[0]!.mensaje).toBe("Falló la fila 0");
  });

  it("respeta los topes del backend: 20 grupos por lote y mensajes de 300 caracteres", async () => {
    const lotes = capturar();
    for (let i = 0; i < 25; i++) registrarError("error", `Error distinto ${"x".repeat(i)}`);
    registrarError("limite", "m".repeat(500));
    enviarPendientes();

    await esperarEnvios(lotes, 2);
    expect(lotes.map((l) => l.cuerpo.errores.length)).toEqual([20, 6]);
    const largo = lotes.flatMap((l) => l.cuerpo.errores).find((e) => e.tipo === "limite")!;
    expect(largo.mensaje).toHaveLength(300);
  });

  it("los errores sin información útil no se envían", async () => {
    const lotes = capturar();
    registrarError("error", "ResizeObserver loop completed with undelivered notifications.");
    registrarError("error", "Script error.");
    enviarPendientes();
    await new Promise((r) => setTimeout(r, 50));
    expect(lotes).toHaveLength(0);
  });

  it("con la pestaña oculta se envía en el momento (los temporizadores ya no corren)", async () => {
    const lotes = capturar();
    const visibilidad = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    registrarVital({ nombre: "LCP", valor: 1800, calificacion: "good", ruta: "/tickets/KC-9" });
    visibilidad.mockRestore();
    await esperarEnvios(lotes, 1);
    expect(lotes[0]!.cuerpo.vitals).toEqual([
      { nombre: "LCP", valor: 1800, calificacion: "good", ruta: "/tickets/:numero" },
    ]);
  });

  it("con sesión lleva el token; sin sesión va igual (el login también falla)", async () => {
    const lotes = capturar();
    registrarError("error", "sin sesión");
    enviarPendientes();
    await esperarEnvios(lotes, 1);
    expect(lotes[0]!.autorizacion).toBeNull();

    fijarSesion("acc_telemetria", "ref_1");
    registrarError("error", "con sesión");
    enviarPendientes();
    await esperarEnvios(lotes, 2);
    expect(lotes[1]!.autorizacion).toBe("Bearer acc_telemetria");
  });

  it("si el envío falla, el portal sigue (no lanza)", async () => {
    server.use(http.post("*/api/v1/telemetry/client", () => HttpResponse.error()));
    registrarError("error", "algo");
    expect(() => enviarPendientes()).not.toThrow();
    await new Promise((r) => setTimeout(r, 50));
  });
});

describe("de dónde salen los errores", () => {
  it("lo que atrapa un <LimiteError> va como «limite»", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const lotes = capturar();
    reportarErrorCliente({
      id: "err_1",
      nivel: "ruta",
      ruta: "/consumo",
      mensaje: "Cannot read properties of undefined",
      pilaComponentes: "\n    at Consumo (Consumo.tsx:40)\n    at LimiteError",
    });
    enviarPendientes();
    await esperarEnvios(lotes, 1);
    expect(lotes[0]!.cuerpo.errores[0]).toMatchObject({ tipo: "limite", veces: 1 });
  });

  it("una respuesta que no cumple el contrato de la API va como «contrato»", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const lotes = capturar();
    server.use(http.get("*/api/v1/organization", () => HttpResponse.json({ id: 42 })));
    fijarSesion("acc_1", "ref_1");
    await expect(
      api.get("/api/v1/organization", { esquema: z.object({ id: z.string() }) }),
    ).rejects.toMatchObject({ codigo: "RESPUESTA_INVALIDA" });
    enviarPendientes();
    await esperarEnvios(lotes, 1);
    expect(lotes[0]!.cuerpo.errores[0]).toMatchObject({ tipo: "contrato" });
    expect(lotes[0]!.cuerpo.errores[0]!.mensaje).toContain("/api/v1/organization");
  });

  it("los errores y rechazos no manejados de la página se escuchan", async () => {
    const lotes = capturar();
    iniciarTelemetria();
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "boom",
        filename: "https://x/assets/Tablero-abc.js",
        lineno: 12,
      }),
    );
    const rechazo = new Event("unhandledrejection");
    Object.assign(rechazo, { reason: new Error("promesa rota") });
    window.dispatchEvent(rechazo);
    enviarPendientes();
    await esperarEnvios(lotes, 1);
    expect(lotes[0]!.cuerpo.errores.map((e) => [e.tipo, e.mensaje])).toEqual([
      ["error", "boom"],
      ["rechazo", "promesa rota"],
    ]);
  });
});
