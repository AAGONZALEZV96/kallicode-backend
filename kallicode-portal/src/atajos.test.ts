/** Resolución de atajos de teclado (KC-PT-11-6). */
import { describe, expect, it } from "vitest";

import { ATAJOS, esEditable, resolverTecla } from "./atajos";

describe("resolverTecla", () => {
  it("una tecla sola: ayuda, buscador y nuevo ticket", () => {
    expect(resolverTecla("?", null).accion).toEqual({ tipo: "ayuda" });
    expect(resolverTecla("/", null).accion).toEqual({ tipo: "buscar" });
    expect(resolverTecla("n", null).accion).toEqual({ tipo: "ir", ruta: "/nuevo-ticket" });
  });

  it("«g» abre una secuencia y la segunda tecla elige la vista", () => {
    expect(resolverTecla("g", null)).toEqual({ accion: null, abre: "g" });
    expect(resolverTecla("t", "g").accion).toEqual({ tipo: "ir", ruta: "/tickets" });
    expect(resolverTecla("e", "g").accion).toEqual({ tipo: "ir", ruta: "/equipo" });
  });

  it("una tecla sin atajo, o una segunda tecla que no corresponde, no hace nada", () => {
    expect(resolverTecla("x", null)).toEqual({ accion: null, abre: null });
    expect(resolverTecla("x", "g")).toEqual({ accion: null, abre: null });
    // Sin «g» antes, «t» no navega.
    expect(resolverTecla("t", null).accion).toBeNull();
  });

  it("no hay dos atajos con las mismas teclas", () => {
    const teclas = ATAJOS.map((a) => a.teclas.join(" "));
    expect(new Set(teclas).size).toBe(teclas.length);
  });
});

describe("esEditable", () => {
  it("campos de texto, áreas, listas y contenido editable cuentan como escritura", () => {
    const texto = document.createElement("input");
    const busqueda = Object.assign(document.createElement("input"), { type: "search" });
    const casilla = Object.assign(document.createElement("input"), { type: "checkbox" });
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    expect(esEditable(texto)).toBe(true);
    expect(esEditable(busqueda)).toBe(true);
    expect(esEditable(document.createElement("textarea"))).toBe(true);
    expect(esEditable(document.createElement("select"))).toBe(true);
    expect(esEditable(casilla)).toBe(false);
    expect(esEditable(document.createElement("button"))).toBe(false);
    expect(esEditable(document.body)).toBe(false);
    expect(esEditable(null)).toBe(false);
  });
});
