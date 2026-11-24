/**
 * Contraste de los tokens de color en el tema claro y el oscuro (KC-PT-10-2, WCAG 2.1 AA).
 *
 * - Texto (1.4.3): 4,5:1 para cada color de texto sobre cada fondo en que aparece.
 * - Componentes y foco (1.4.11): 3:1 para el borde de los campos y el contorno de foco.
 * Lee los valores de estilos/tokens.css: cambiar un color que deje de cumplir hace fallar la prueba.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve("src/estilos/tokens.css"), "utf8");

/** Variables de un bloque (`:root {…}` o `body.dark {…}`). */
function tokens(selector: string): Record<string, string> {
  const inicio = CSS.indexOf(`${selector} {`);
  const bloque = CSS.slice(inicio, CSS.indexOf("}", inicio));
  return Object.fromEntries(
    [...bloque.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)].map((m) => [
      m[1]!,
      m[2]!.toLowerCase(),
    ]),
  );
}

const luminancia = (hex: string) => {
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
};
const contraste = (a: string, b: string) => {
  const [claro, oscuro] = [luminancia(a), luminancia(b)].sort((x, y) => y - x);
  return (claro! + 0.05) / (oscuro! + 0.05);
};

const CLARO = tokens(":root");
// El tema oscuro solo redefine colores: lo demás lo hereda del claro.
const OSCURO = { ...CLARO, ...tokens("body.dark") };

const FONDOS = ["bg", "surface", "surface2", "chip"];
const TEXTOS = ["text", "muted", "ok", "warn", "err", "gate", "accent"];

/** Pares que no llegan al mínimo, como «warn sobre chip 3.45:1». */
function fallas(t: Record<string, string>, pares: [string, string][], minimo: number) {
  return pares
    .map(([frente, fondo]) => [frente, fondo, contraste(t[frente]!, t[fondo]!)] as const)
    .filter(([, , r]) => r < minimo)
    .map(([frente, fondo, r]) => `${frente} sobre ${fondo} ${r.toFixed(2)}:1`);
}

describe.each([
  ["claro", CLARO],
  ["oscuro", OSCURO],
])("tokens del tema %s", (_, t) => {
  it("tiene todos los colores que se comprueban", () => {
    for (const nombre of [...FONDOS, ...TEXTOS, "accent-text", "on-color", "control"])
      expect(t[nombre], nombre).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("cada color de texto llega a 4,5:1 sobre cada fondo", () => {
    const pares = TEXTOS.flatMap((frente) =>
      FONDOS.map((fondo): [string, string] => [frente, fondo]),
    );
    expect(fallas(t, pares, 4.5)).toEqual([]);
  });

  it("el texto sobre los fondos de color llenos llega a 4,5:1", () => {
    expect(
      fallas(
        t,
        [
          ["accent-text", "accent"],
          ["on-color", "ok"],
          ["on-color", "gate"],
        ],
        4.5,
      ),
    ).toEqual([]);
  });

  it("el borde de los campos y el contorno de foco llegan a 3:1 con los fondos", () => {
    const pares = ["control", "gate"].flatMap((frente) =>
      ["bg", "surface", "surface2"].map((fondo): [string, string] => [frente, fondo]),
    );
    expect(fallas(t, pares, 3)).toEqual([]);
  });
});

it("ninguna regla escribe texto blanco fijo: depende del tema (--on-color)", () => {
  expect(CSS.match(/^\s*color:\s*(#fff\b|#ffffff\b|white\b)/gim) ?? []).toEqual([]);
});
