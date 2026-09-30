/** Arranque común de Vitest: matchers de jest-dom y dobles MSW. */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { limpiarSesion } from "../api/client";
import { server } from "../mocks/server";

// Una petición sin doble declarado es un fallo: evita llamadas reales por accidente.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  limpiarSesion();
  sessionStorage.clear();
  localStorage.clear();
});

afterAll(() => server.close());
