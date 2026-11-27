/**
 * Web Vitals (KC-PT-11-2, KC-PT-11-5): se registran las cinco métricas y cada una va a la
 * telemetría del portal sin datos del cliente; también quedan en window.__kcWebVitals.
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

import { iniciarWebVitals } from "./webVitals";

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
    iniciarWebVitals({ registrar: vi.fn() });
    expect([...registradas.keys()].sort()).toEqual(["CLS", "FCP", "INP", "LCP", "TTFB"]);
  });

  it("cada métrica va a la telemetría con lo que acepta el backend y la ruta sin el número", () => {
    const registrar = vi.fn();
    iniciarWebVitals({ registrar });
    registradas.get("LCP")!(metrica("LCP", 1234.56));
    registradas.get("CLS")!(metrica("CLS", 0.012345));

    expect(registrar.mock.calls.map(([v]) => v)).toEqual([
      { nombre: "LCP", valor: 1235, calificacion: "good", ruta: "/tickets/:numero" },
      { nombre: "CLS", valor: 0.0123, calificacion: "good", ruta: "/tickets/:numero" },
    ]);
    expect(window.__kcWebVitals?.map((m) => `${m.nombre}:${m.id}`)).toEqual([
      "LCP:v5-LCP",
      "CLS:v5-CLS",
    ]);
  });
});
