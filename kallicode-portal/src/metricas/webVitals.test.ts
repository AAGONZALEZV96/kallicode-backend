/**
 * Web Vitals (KC-PT-11-2): se registran las cinco métricas, se envían al destino configurado
 * sin datos del cliente y quedan en window.__kcWebVitals.
 */
import type { Metric } from "web-vitals";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const registradas = vi.hoisted(() => new Map<string, (m: Metric) => void>());
vi.mock("web-vitals", () => ({
  onLCP: (cb: (m: Metric) => void) => registradas.set("LCP", cb),
  onINP: (cb: (m: Metric) => void) => registradas.set("INP", cb),
  onCLS: (cb: (m: Metric) => void) => registradas.set("CLS", cb),
  onFCP: (cb: (m: Metric) => void) => registradas.set("FCP", cb),
  onTTFB: (cb: (m: Metric) => void) => registradas.set("TTFB", cb),
}));

import { iniciarWebVitals, rutaGenerica } from "./webVitals";

function metrica(name: Metric["name"], value: number): Metric {
  return {
    name,
    value,
    rating: "good",
    delta: value,
    id: `v5-${name}`,
    entries: [],
    navigationType: "navigate",
  } as unknown as Metric;
}

beforeEach(() => {
  registradas.clear();
  window.history.pushState({}, "", "/tickets/KC-1003");
});
afterEach(() => {
  window.history.pushState({}, "", "/");
  delete window.__kcWebVitals;
});

describe("Web Vitals", () => {
  it("registra LCP, INP, CLS, FCP y TTFB", () => {
    iniciarWebVitals({ destino: undefined });
    expect([...registradas.keys()].sort()).toEqual(["CLS", "FCP", "INP", "LCP", "TTFB"]);
  });

  it("envía cada métrica al destino, con la ruta sin el número del ticket", () => {
    const enviar = vi.fn();
    iniciarWebVitals({ destino: "https://telemetria.test/vitals", enviar });
    registradas.get("LCP")!(metrica("LCP", 1234.56));
    registradas.get("CLS")!(metrica("CLS", 0.012345));

    expect(enviar).toHaveBeenCalledTimes(2);
    const [url, cuerpo] = enviar.mock.calls[0]!;
    expect(url).toBe("https://telemetria.test/vitals");
    expect(JSON.parse(cuerpo)).toEqual({
      nombre: "LCP",
      valor: 1235,
      calificacion: "good",
      id: "v5-LCP",
      navegacion: "navigate",
      ruta: "/tickets/:numero",
    });
    expect(JSON.parse(enviar.mock.calls[1]![1]).valor).toBe(0.0123);
    expect(window.__kcWebVitals?.map((m) => m.nombre)).toEqual(["LCP", "CLS"]);
  });

  it("sin destino configurado no envía nada, pero las deja en window.__kcWebVitals", () => {
    const enviar = vi.fn();
    vi.spyOn(console, "info").mockImplementation(() => {});
    iniciarWebVitals({ destino: undefined, enviar });
    registradas.get("TTFB")!(metrica("TTFB", 80));
    expect(enviar).not.toHaveBeenCalled();
    expect(window.__kcWebVitals).toHaveLength(1);
  });

  it("la ruta genérica quita los números de ticket y deja el resto", () => {
    expect(rutaGenerica("/tickets/KC-1003")).toBe("/tickets/:numero");
    expect(rutaGenerica("/auditoria/kc-42")).toBe("/auditoria/:numero");
    expect(rutaGenerica("/consumo")).toBe("/consumo");
    expect(rutaGenerica("/tickets")).toBe("/tickets");
  });
});
