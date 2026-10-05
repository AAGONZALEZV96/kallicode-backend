/** Arranque común de Vitest: matchers de jest-dom y dobles MSW. */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { limpiarSesion } from "../api/client";
import { server } from "../mocks/server";

/**
 * jsdom reemplaza AbortController, pero el fetch de Node (undici) exige su propio
 * AbortSignal y rechaza el de jsdom. En el navegador hay uno solo, así que esto solo
 * afecta a las pruebas: se quita la señal antes de llegar a Node y se reproduce la
 * cancelación rechazando con AbortError, como haría el navegador.
 */
function fetchConSenalDeJsdom(fetchNodo: typeof fetch): typeof fetch {
  return (entrada, init) => {
    const senal = init?.signal;
    if (!senal) return fetchNodo(entrada, init);
    return new Promise<Response>((resolver, rechazar) => {
      const abortar = () => rechazar(new DOMException("La petición se canceló.", "AbortError"));
      if (senal.aborted) return abortar();
      senal.addEventListener("abort", abortar, { once: true });
      fetchNodo(entrada, { ...init, signal: undefined })
        .then(resolver, rechazar)
        .finally(() => senal.removeEventListener("abort", abortar));
    });
  };
}

// Una petición sin doble declarado es un fallo: evita llamadas reales por accidente.
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  // Después de listen: así envuelve el fetch que intercepta MSW.
  globalThis.fetch = fetchConSenalDeJsdom(globalThis.fetch);
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  limpiarSesion();
  sessionStorage.clear();
  localStorage.clear();
});

afterAll(() => server.close());
