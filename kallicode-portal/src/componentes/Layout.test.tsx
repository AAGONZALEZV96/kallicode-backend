/**
 * Pulido responsive del layout (KC-PT-10-1): cada vista empieza arriba, el menú horizontal
 * del celular muestra la vista activa y el botón de tema conserva su nombre con solo el ícono.
 * jsdom no calcula el layout: las medidas se fijan a mano en los elementos.
 */
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { entrarDesde } from "../test/portal";

/** Reemplaza una propiedad de solo lectura de jsdom por un valor fijo o editable. */
function fijar(el: Element, propiedad: string, valor: number) {
  let actual = valor;
  Object.defineProperty(el, propiedad, {
    configurable: true,
    get: () => actual,
    set: (v: number) => (actual = v),
  });
}

function caja(el: Element, left: number, width: number) {
  const rect = new DOMRect(left, 0, width, 40);
  el.getBoundingClientRect = () => rect;
}

describe("layout en pantallas angostas", () => {
  it("al cambiar de vista el contenido vuelve arriba (no hereda el scroll de la anterior)", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await screen.findByTestId("nuevo-ticket.vista.raiz");
    const contenido = document.getElementById("contenido")!;
    fijar(contenido, "scrollTop", 900);

    await usuario.click(screen.getByTestId("layout.nav.nueva-funcionalidad"));
    await screen.findByTestId("nueva-funcionalidad.vista.raiz");

    expect(contenido.scrollTop).toBe(0);
  });

  it("el menú horizontal se desplaza hasta centrar la vista activa", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    const menu = document.querySelector("nav.kc")!;
    fijar(menu, "scrollWidth", 1600);
    fijar(menu, "clientWidth", 375);
    fijar(menu, "scrollLeft", 0);
    caja(menu, 0, 375);
    // Equipo queda fuera de la pantalla, a 1200 px del borde del menú.
    caja(screen.getByTestId("layout.nav.equipo"), 1200, 90);

    await usuario.click(screen.getByTestId("layout.nav.equipo"));
    await screen.findByTestId("equipo.vista.raiz");

    expect(menu.scrollLeft).toBe(1200 - (375 - 90) / 2);
  });

  it("con el menú lateral (todo cabe) no se desplaza", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    const menu = document.querySelector("nav.kc")!;
    fijar(menu, "scrollWidth", 220);
    fijar(menu, "clientWidth", 220);
    fijar(menu, "scrollLeft", 0);
    caja(screen.getByTestId("layout.nav.equipo"), 400, 90);

    await usuario.click(screen.getByTestId("layout.nav.equipo"));
    await screen.findByTestId("equipo.vista.raiz");

    expect(menu.scrollLeft).toBe(0);
  });

  it("el botón de tema se llama por su texto aunque en el celular solo se vea el ícono", async () => {
    const usuario = await entrarDesde("/");
    const tema = await screen.findByTestId("layout.cabecera.tema");
    expect(tema).toHaveAccessibleName("Oscuro");
    expect(tema.querySelector(".hdr-etiqueta")).toHaveTextContent("Oscuro");

    await usuario.click(tema);

    expect(tema).toHaveAccessibleName("Claro");
    expect(tema).toHaveAttribute("aria-pressed", "true");
  });
});

describe("contenido largo sin nada enfocable (KC-PT-10-2)", () => {
  afterEach(() => vi.restoreAllMocks());

  /** La vista es más alta que lo visible; lo demás cabe (jsdom no mide). */
  function contenidoLargo() {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.id === "contenido" ? 2000 : 0;
    });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(800);
  }

  it("<main> entra en el orden de Tab para poder desplazarlo con el teclado (Modelos IA)", async () => {
    contenidoLargo();
    await entrarDesde("/modelos");
    await screen.findByTestId("modelos-ia.asignaciones.tabla");
    expect(document.getElementById("contenido")).toHaveAttribute("tabindex", "0");
  });

  it("con enlaces o botones en el contenido, <main> no agrega una parada de Tab", async () => {
    contenidoLargo();
    await entrarDesde("/tickets");
    await screen.findAllByTestId("tickets.lista.fila");
    expect(document.getElementById("contenido")).toHaveAttribute("tabindex", "-1");
  });
});
