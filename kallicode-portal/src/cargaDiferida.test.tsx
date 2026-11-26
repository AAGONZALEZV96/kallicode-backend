/**
 * cargaDiferida (KC-PT-11-1): si la descarga de una vista falla (archivo de una versión anterior
 * tras un despliegue), se recarga la página; si vuelve a fallar poco después de recargar, el
 * error sube al límite de error en vez de recargar sin fin.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { Component, Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cargaDiferida, ESPERA_ENTRE_RECARGAS_MS, MARCA_RECARGA, navegador } from "./cargaDiferida";

class Limite extends Component<{ children: ReactNode }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? <p>falló la descarga</p> : this.props.children;
  }
}

function montar(importar: () => Promise<{ default: () => ReactNode }>) {
  const Vista = cargaDiferida(importar);
  render(
    <Limite>
      <Suspense fallback={<p>cargando</p>}>
        <Vista />
      </Suspense>
    </Limite>,
  );
}

const falla = () => Promise.reject(new Error("Failed to fetch dynamically imported module"));
let recargar: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  recargar = vi.spyOn(navegador, "recargar").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("cargaDiferida", () => {
  it("monta la vista cuando la descarga funciona", async () => {
    montar(() => Promise.resolve({ default: () => <h1>Vista lista</h1> }));
    expect(await screen.findByRole("heading", { name: "Vista lista" })).toBeInTheDocument();
    expect(recargar).not.toHaveBeenCalled();
  });

  it("si la descarga falla, recarga la página (trae la versión nueva) y anota cuándo", async () => {
    montar(falla);
    await waitFor(() => expect(recargar).toHaveBeenCalledTimes(1));
    expect(Number(sessionStorage.getItem(MARCA_RECARGA))).toBeGreaterThan(Date.now() - 5000);
    expect(screen.getByText("cargando")).toBeInTheDocument();
  });

  it("si vuelve a fallar poco después de recargar, el error llega al límite (sin bucle)", async () => {
    sessionStorage.setItem(MARCA_RECARGA, String(Date.now() - 1000));
    montar(falla);
    expect(await screen.findByText("falló la descarga")).toBeInTheDocument();
    expect(recargar).not.toHaveBeenCalled();
  });

  it("una vista que sí carga no habilita otra recarga inmediata", async () => {
    sessionStorage.setItem(MARCA_RECARGA, String(Date.now() - 1000));
    montar(() => Promise.resolve({ default: () => <h1>Layout</h1> }));
    await screen.findByRole("heading", { name: "Layout" });
    montar(falla);
    expect(await screen.findByText("falló la descarga")).toBeInTheDocument();
    expect(recargar).not.toHaveBeenCalled();
  });

  it("pasado el margen desde la última recarga, otra versión nueva vuelve a recargar", async () => {
    sessionStorage.setItem(MARCA_RECARGA, String(Date.now() - ESPERA_ENTRE_RECARGAS_MS - 1));
    montar(falla);
    await waitFor(() => expect(recargar).toHaveBeenCalledTimes(1));
  });
});
