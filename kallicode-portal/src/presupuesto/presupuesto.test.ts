/**
 * Cálculo del presupuesto del paquete (KC-PT-11-2) sobre un manifiesto de Vite de ejemplo. La
 * comprobación sobre el build real corre en CI: npm run build && npm run presupuesto.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  cierre,
  ENTRADAS_TABLERO,
  medirPaquete,
  PRESUPUESTOS,
  type Manifiesto,
} from "./presupuesto";

const MANIFIESTO: Manifiesto = {
  "index.html": {
    file: "assets/index.js",
    css: ["assets/index.css"],
    imports: ["_vendor-react.js", "_vendor-zod.js"],
    isEntry: true,
  },
  "_vendor-react.js": { file: "assets/vendor-react.js" },
  "_vendor-zod.js": { file: "assets/vendor-zod.js" },
  "_formato.js": { file: "assets/formato.js", imports: ["_vendor-react.js"] },
  "src/componentes/Layout.tsx": {
    file: "assets/Layout.js",
    imports: ["_vendor-react.js", "_formato.js"],
  },
  "src/vistas/Tablero.tsx": { file: "assets/Tablero.js", imports: ["_formato.js"] },
  "src/vistas/Equipo.tsx": { file: "assets/Equipo.js", imports: ["_formato.js"] },
  "src/estado/diccionarios/en.ts": { file: "assets/en.js" },
};

const TAMANOS: Record<string, number> = {
  "index.html": 400,
  "assets/index.js": 30_000,
  "assets/index.css": 7_000,
  "assets/vendor-react.js": 46_000,
  "assets/vendor-zod.js": 27_000,
  "assets/formato.js": 1_300,
  "assets/Layout.js": 4_000,
  "assets/Tablero.js": 1_500,
  "assets/Equipo.js": 2_800,
  "assets/en.js": 13_000,
};

describe("presupuesto del paquete", () => {
  it("el cierre de una entrada sigue sus imports estáticos sin repetir archivos", () => {
    expect([...cierre(MANIFIESTO, "src/componentes/Layout.tsx")].sort()).toEqual([
      "assets/Layout.js",
      "assets/formato.js",
      "assets/vendor-react.js",
    ]);
  });

  it("el login es index.html y lo que importa; el tablero suma layout y tablero, no otras vistas ni idiomas", () => {
    const [login, tablero] = medirPaquete(MANIFIESTO, (a) => TAMANOS[a]!);
    expect(login!.archivos).toEqual([
      "assets/index.css",
      "assets/index.js",
      "assets/vendor-react.js",
      "assets/vendor-zod.js",
      "index.html",
    ]);
    expect(login!.bytes).toBe(400 + 30_000 + 7_000 + 46_000 + 27_000);
    expect(tablero!.bytes).toBe(login!.bytes + 4_000 + 1_500 + 1_300);
    expect(tablero!.archivos).not.toContain("assets/Equipo.js");
    expect(tablero!.archivos).not.toContain("assets/en.js");
    expect([login!.tope, tablero!.tope]).toEqual([PRESUPUESTOS.login, PRESUPUESTOS.tablero]);
  });

  it("una entrada que no está en el manifiesto es un error claro (p. ej. si se renombra Tablero.tsx)", () => {
    const sinTablero = Object.fromEntries(
      Object.entries(MANIFIESTO).filter(([clave]) => clave !== "src/vistas/Tablero.tsx"),
    );
    expect(() => medirPaquete(sinTablero, () => 0)).toThrow(/src\/vistas\/Tablero\.tsx/);
  });

  it("los topes son los del ticket: 180 kB el login y 280 kB el tablero", () => {
    expect(PRESUPUESTOS).toEqual({ login: 180_000, tablero: 280_000 });
  });

  it("las entradas del tablero existen y son las que se cargan bajo demanda", () => {
    const diferidas = readFileSync(resolve("src/vistasDiferidas.ts"), "utf8");
    for (const entrada of ENTRADAS_TABLERO) {
      expect(existsSync(resolve(entrada)), entrada).toBe(true);
      const modulo = entrada.replace(/^src\//, "./").replace(/\.tsx$/, "");
      expect(diferidas).toContain(`import("${modulo}")`);
    }
  });
});
