/**
 * Script anti-destello de index.html (KC-PT-11-5): aplica el tema guardado antes del primer
 * pintado, sin esperar a React. Va antes de #root y no rompe si localStorage no está disponible.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

const HTML = readFileSync(resolve("index.html"), "utf8");
const SCRIPT = /<script>([\s\S]*?)<\/script>/.exec(HTML)?.[1] ?? "";
const ejecutar = () => new Function(SCRIPT)();

afterEach(() => {
  document.body.classList.remove("dark");
  vi.restoreAllMocks();
});

describe("script anti-destello", () => {
  it("es un script en línea antes de #root y del módulo de la aplicación", () => {
    expect(SCRIPT).toContain("kc_tema");
    expect(HTML.indexOf(SCRIPT)).toBeLessThan(HTML.indexOf('<div id="root">'));
    expect(HTML.indexOf(SCRIPT)).toBeLessThan(HTML.indexOf('type="module"'));
  });

  it("con el tema oscuro guardado, el body arranca en oscuro", () => {
    localStorage.setItem("kc_tema", "dark");
    ejecutar();
    expect(document.body).toHaveClass("dark");
  });

  it("con el claro o sin preferencia, queda en claro", () => {
    localStorage.setItem("kc_tema", "light");
    ejecutar();
    expect(document.body).not.toHaveClass("dark");
    localStorage.removeItem("kc_tema");
    ejecutar();
    expect(document.body).not.toHaveClass("dark");
  });

  it("sin localStorage (bloqueado por el navegador) no lanza", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(ejecutar).not.toThrow();
  });
});
