/**
 * Desplazable (KC-PT-10-2): mientras su contenido no cabe es una región con nombre que se
 * enfoca con Tab; si cabe, no agrega una parada de Tab. jsdom no mide: los tamaños se fijan a mano.
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Desplazable from "./Desplazable";

let alCambiarTamano: (() => void) | null = null;
class ResizeObserverDePrueba {
  constructor(cb: () => void) {
    alCambiarTamano = cb;
  }
  observe() {}
  disconnect() {}
}

/** Fija el ancho del contenido y del contenedor antes de que el componente los mida. */
function medidas(contenido: number, visible: number) {
  vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(contenido);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(visible);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  alCambiarTamano = null;
});

function montar() {
  render(
    <Desplazable etiqueta="Consumo por función" className="extra">
      <table>
        <tbody>
          <tr>
            <td>celda</td>
          </tr>
        </tbody>
      </table>
    </Desplazable>,
  );
  return screen.getByText("celda").closest(".desplazable")!;
}

describe("Desplazable", () => {
  it("si el contenido cabe, no es una región ni una parada de Tab", () => {
    medidas(300, 300);
    const zona = montar();
    expect(zona).not.toHaveAttribute("tabindex");
    expect(zona).not.toHaveAttribute("role");
    expect(zona).toHaveClass("desplazable", "extra");
  });

  it("si el contenido no cabe, es una región con nombre que se enfoca con Tab", () => {
    medidas(900, 340);
    montar();
    const zona = screen.getByRole("region", { name: "Consumo por función" });
    expect(zona).toHaveAttribute("tabindex", "0");
  });

  it("vuelve a medir al cambiar de tamaño (girar el teléfono, cerrar el menú)", () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverDePrueba);
    medidas(300, 300);
    montar();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();

    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(200);
    act(() => alCambiarTamano!());
    expect(screen.getByRole("region", { name: "Consumo por función" })).toBeInTheDocument();

    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(320);
    act(() => alCambiarTamano!());
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});
