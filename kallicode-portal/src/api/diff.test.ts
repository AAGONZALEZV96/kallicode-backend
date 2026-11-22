import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import { parsearDiff, verificarDiff } from "./diff";

afterEach(() => vi.unstubAllGlobals());

const DIFF = [
  "diff --git a/app.py b/app.py",
  "--- a/app.py",
  "+++ b/app.py",
  "@@ -10,4 +10,5 @@ def f():",
  "     uno",
  "-    dos",
  "+    DOS",
  "+    tres",
  "     cuatro",
  "@@ -40,2 +41,2 @@",
  "-viejo",
  "+nuevo",
  "\\ No newline at end of file",
  "",
].join("\n");

const sha = (texto: string) => createHash("sha256").update(texto, "utf8").digest("hex");

describe("parsearDiff", () => {
  it("numera cada línea en la versión anterior y en la nueva", () => {
    const { lineas, agregadas, eliminadas } = parsearDiff(DIFF);

    expect([agregadas, eliminadas]).toEqual([3, 2]);
    expect(lineas.map((l) => [l.tipo, l.antes, l.despues, l.texto])).toEqual([
      ["cabecera", null, null, "diff --git a/app.py b/app.py"],
      ["cabecera", null, null, "--- a/app.py"],
      ["cabecera", null, null, "+++ b/app.py"],
      ["bloque", null, null, "@@ -10,4 +10,5 @@ def f():"],
      ["contexto", 10, 10, "    uno"],
      ["eliminada", 11, null, "    dos"],
      ["agregada", null, 11, "    DOS"],
      ["agregada", null, 12, "    tres"],
      ["contexto", 12, 13, "    cuatro"],
      ["bloque", null, null, "@@ -40,2 +41,2 @@"],
      ["eliminada", 40, null, "viejo"],
      ["agregada", null, 41, "nuevo"],
      ["aviso", null, null, "\\ No newline at end of file"],
    ]);
  });

  it("un segundo archivo en el mismo diff no se cuenta como líneas borradas", () => {
    const dos = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-c\n+d\n";
    const { lineas, agregadas, eliminadas } = parsearDiff(dos);

    expect([agregadas, eliminadas]).toEqual([2, 2]);
    expect(lineas.filter((l) => l.tipo === "cabecera").map((l) => l.texto)).toEqual([
      "--- a/x",
      "+++ b/x",
      "--- a/y",
      "+++ b/y",
    ]);
  });

  it("sin bloques @@ todo es cabecera (no inventa números)", () => {
    const { lineas, agregadas } = parsearDiff("Binary files a/x.png and b/x.png differ\n");
    expect(lineas).toEqual([
      {
        tipo: "cabecera",
        texto: "Binary files a/x.png and b/x.png differ",
        antes: null,
        despues: null,
      },
    ]);
    expect(agregadas).toBe(0);
  });
});

describe("verificarDiff", () => {
  it("coincide con el SHA-256 del texto en UTF-8, como lo calcula el backend", async () => {
    const conAcentos = "+ añadió «comillas» y €\n";
    expect(await verificarDiff(conAcentos, sha(conAcentos))).toBe("coincide");
  });

  it("un diff cambiado ya no coincide", async () => {
    expect(await verificarDiff(DIFF + " ", sha(DIFF))).toBe("no-coincide");
  });

  it("sin Web Crypto no afirma nada", async () => {
    vi.stubGlobal("crypto", {});
    expect(await verificarDiff(DIFF, sha(DIFF))).toBe("no-disponible");
  });
});
