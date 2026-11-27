/**
 * Sin estilos en línea fijos (KC-PT-11-4): lo que es igual en cada render va en una clase de
 * estilos/tokens.css (utilidades al final del archivo). En línea solo quedan valores que se
 * calculan al pintar, como el ancho de una barra de progreso (`width: \`${pct}%\``).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const RAIZ = resolve("src");

function vistasYComponentes(dir: string): string[] {
  return readdirSync(dir).flatMap((nombre) => {
    const ruta = join(dir, nombre);
    if (statSync(ruta).isDirectory())
      return ["mocks", "test", "generado"].includes(nombre) ? [] : vistasYComponentes(ruta);
    return nombre.endsWith(".tsx") && !nombre.endsWith(".test.tsx") ? [ruta] : [];
  });
}

/** ¿El valor es fijo (un literal) y no algo que se calcula? */
const esFijo = (valor: ts.Expression) =>
  ts.isStringLiteral(valor) ||
  ts.isNumericLiteral(valor) ||
  ts.isNoSubstitutionTemplateLiteral(valor) ||
  (ts.isPrefixUnaryExpression(valor) && ts.isNumericLiteral(valor.operand));

function estilosEnLinea() {
  const fijos: string[] = [];
  const calculados: string[] = [];
  for (const ruta of vistasYComponentes(RAIZ)) {
    const fuente = ts.createSourceFile(
      ruta,
      readFileSync(ruta, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const archivo = relative(RAIZ, ruta).split(sep).join("/");
    const visitar = (nodo: ts.Node) => {
      if (ts.isJsxAttribute(nodo) && nodo.name.getText() === "style") {
        const { line } = fuente.getLineAndCharacterOfPosition(nodo.getStart());
        const donde = `${archivo}:${line + 1} ${nodo.getText().replace(/\s+/g, " ")}`;
        const valor =
          nodo.initializer && ts.isJsxExpression(nodo.initializer)
            ? nodo.initializer.expression
            : undefined;
        // Solo un objeto con todos sus valores calculados; un objeto guardado en una constante
        // (style={etiqueta}) también es fijo.
        const soloCalculados =
          valor &&
          ts.isObjectLiteralExpression(valor) &&
          valor.properties.every((p) => ts.isPropertyAssignment(p) && !esFijo(p.initializer));
        (soloCalculados ? calculados : fijos).push(donde);
      }
      ts.forEachChild(nodo, visitar);
    };
    visitar(fuente);
  }
  return { fijos, calculados };
}

describe("estilos en línea (KC-PT-11-4)", () => {
  const { fijos, calculados } = estilosEnLinea();

  it("ninguna vista ni componente usa un estilo en línea fijo: va en una clase de tokens.css", () => {
    expect(fijos).toEqual([]);
  });

  it("los que quedan son valores calculados (anchos de barras de progreso y del esqueleto)", () => {
    expect(calculados.length).toBeLessThanOrEqual(5);
    for (const estilo of calculados) expect(estilo).toMatch(/style=\{\{ width: /);
  });

  it("las utilidades están definidas al final de tokens.css, con la prioridad de un estilo en línea", () => {
    const css = readFileSync(resolve("src/estilos/tokens.css"), "utf8");
    const inicio = css.indexOf("---- utilidades (KC-PT-11-4)");
    expect(inicio).toBeGreaterThan(0);
    const utilidades = css.slice(inicio);
    for (const clase of ["mb-16", "ancho-auto", "texto-tenue", "fs-12", "texto-derecha", "fila"])
      expect(utilidades).toMatch(new RegExp(`\\.${clase} \\{[^}]*!important`));
  });
});
