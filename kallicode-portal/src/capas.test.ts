/**
 * Capa de servicios y tipado sin aserciones (KC-PT-11-3, cierra PT-F19).
 *
 * - Ningún archivo del portal (fuera de las pruebas, los dobles y los tipos generados) afirma un
 *   tipo con `x as T` o `<T>x`: se comprueba en tiempo de ejecución (src/tipado.ts, guardas de
 *   tipo) o se valida con el esquema Zod de la respuesta. `as const` sí se permite.
 * - Las vistas no hablan con el cliente HTTP: usan la capa de servicios (api/consultas,
 *   api/errores, api/erroresCampo…), que es la que conoce rutas, esquemas y errores.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const RAIZ = resolve("src");
const EXCLUIDAS = new Set(["generado", "mocks", "test"]);

function fuentes(dir: string): string[] {
  return readdirSync(dir).flatMap((nombre) => {
    const ruta = join(dir, nombre);
    if (statSync(ruta).isDirectory()) return EXCLUIDAS.has(nombre) ? [] : fuentes(ruta);
    return /\.tsx?$/.test(nombre) && !/\.test\.tsx?$/.test(nombre) && !nombre.endsWith(".d.ts")
      ? [ruta]
      : [];
  });
}

const relativa = (ruta: string) => relative(RAIZ, ruta).split(sep).join("/");

function analizar(ruta: string) {
  return ts.createSourceFile(
    ruta,
    readFileSync(ruta, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ruta.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
}

function aserciones(): string[] {
  const hallazgos: string[] = [];
  for (const ruta of fuentes(RAIZ)) {
    const fuente = analizar(ruta);
    const visitar = (nodo: ts.Node) => {
      if (
        (ts.isAsExpression(nodo) || ts.isTypeAssertionExpression(nodo)) &&
        nodo.type.getText() !== "const"
      ) {
        const { line } = fuente.getLineAndCharacterOfPosition(nodo.getStart());
        hallazgos.push(`${relativa(ruta)}:${line + 1} «${nodo.getText().replace(/\s+/g, " ")}»`);
      }
      ts.forEachChild(nodo, visitar);
    };
    visitar(fuente);
  }
  return hallazgos;
}

/** Imports de cada vista hacia el cliente HTTP, y llamadas a fetch. */
function vistasQueUsanElCliente(): string[] {
  const hallazgos: string[] = [];
  for (const ruta of fuentes(join(RAIZ, "vistas"))) {
    const fuente = analizar(ruta);
    const visitar = (nodo: ts.Node) => {
      if (ts.isImportDeclaration(nodo) && ts.isStringLiteral(nodo.moduleSpecifier)) {
        const modulo = nodo.moduleSpecifier.text;
        if (/\/api\/(client|credenciales)$/.test(modulo))
          hallazgos.push(`${relativa(ruta)} importa ${modulo}`);
      }
      if (
        ts.isCallExpression(nodo) &&
        ts.isIdentifier(nodo.expression) &&
        nodo.expression.text === "fetch"
      )
        hallazgos.push(`${relativa(ruta)} llama a fetch`);
      ts.forEachChild(nodo, visitar);
    };
    visitar(fuente);
  }
  return hallazgos;
}

describe("capa de servicios y tipado (KC-PT-11-3)", () => {
  it("recorre el portal y sus 19 vistas", () => {
    const archivos = fuentes(RAIZ).map(relativa);
    expect(archivos).toContain("api/client.ts");
    expect(archivos.filter((a) => /^vistas\/[A-Z]\w+\.tsx$/.test(a)).length).toBeGreaterThanOrEqual(
      19,
    );
  });

  it("ningún archivo afirma un tipo con `as` (salvo `as const`)", () => {
    expect(aserciones()).toEqual([]);
  });

  it("ninguna vista importa el cliente HTTP ni llama a fetch: todo pasa por la capa de servicios", () => {
    expect(vistasQueUsanElCliente()).toEqual([]);
  });
});
