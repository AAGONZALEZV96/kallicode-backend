/**
 * Todos los textos del portal salen de los diccionarios es, en y pt (KC-PT-10-3, cierra PT-F17).
 *
 * - Recorre el código con el analizador de TypeScript y falla si una vista o componente escribe
 *   un texto visible fijo: texto de JSX, atributos que se leen (placeholder, title, aria-label,
 *   alt) o una cadena con palabras dentro de una expresión de JSX (p. ej. en un ternario). Solo
 *   se admiten la marca, siglas y unidades, los nombres de los idiomas en su propio idioma y
 *   términos del producto que no se traducen (EXCEPCIONES).
 * - Cada código de CATALOGO_ERRORES tiene su mensaje traducido (err.* o red.*): sin él, la
 *   interfaz en inglés o portugués mostraría el mensaje del backend, que viene en español.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { DICT } from "./estado/i18n";

const RAIZ = resolve("src");
const EXCLUIDAS = new Set(["mocks", "test", "generado", "locales"]);
const ATRIBUTOS_VISIBLES = new Set(["placeholder", "title", "aria-label", "alt"]);
const LETRAS = /\p{L}{2,}/u;

/** Textos que no se traducen: marca, siglas, unidades, endónimos y términos del producto. */
const EXCEPCIONES = new Set([
  "kallicode",
  "<×>",
  "&lt;×&gt;",
  "LOC",
  "ID",
  "PR",
  "ms",
  "tok ·",
  "Tier",
  "(DB)",
  "Gate",
  "⛨ Gate",
  "⛨ GATE",
  "ES · Español",
  "EN · English",
  "PT · Português",
  "Español",
  "English",
  "Português",
  // Nombres de ramas de git en el mapa de ramas.
  "feature →",
  "staging →",
  // La escalera de tiers de los modelos (nombres propios).
  "flash→pro→fable",
]);

/**
 * Códigos cuyo mensaje del backend trae un detalle que la traducción perdería (qué proveedor
 * rechazó las credenciales, en qué etapa está el ticket, qué campo de orden no vale…): el portal
 * lo muestra tal cual, como hacía CajaError antes de KC-PT-10-3. Ese detalle llega en español;
 * para traducirlo, el backend tendría que mandarlo aparte (en `detalle`).
 */
const CON_DETALLE_DEL_BACKEND = new Set([
  "CREDENCIALES_INVALIDAS",
  "PERMISOS_EXCESIVOS",
  "CONFIG_PROVEEDOR",
  "DEPENDENCIA_NO_DISPONIBLE",
  "TICKET_YA_EN_PIPELINE",
  "SPEC_NO_DISPONIBLE",
  "GATE_NO_ACTIVO",
  "RECURSO_NO_ENCONTRADO",
  "VALIDACION_ENTRADA",
  "TRANSICION_INVALIDA",
]);

function fuentes(dir: string): string[] {
  return readdirSync(dir).flatMap((nombre) => {
    const ruta = join(dir, nombre);
    if (statSync(ruta).isDirectory()) return EXCLUIDAS.has(nombre) ? [] : fuentes(ruta);
    return /\.tsx$/.test(nombre) && !/\.test\.tsx$/.test(nombre) ? [ruta] : [];
  });
}

/**
 * ¿Se muestra la cadena como hijo de JSX? Solo si llega al {…} pasando por lo que devuelve un
 * valor: ramas de un ternario, el lado derecho de && / || / ??, paréntesis o una plantilla. Una
 * cadena que se compara (umbral === "agotado"), indexa o va a una llamada no se muestra.
 */
function seMuestraEnJsx(nodo: ts.Node): boolean {
  let hijo: ts.Node = nodo;
  for (let p = nodo.parent; p; hijo = p, p = p.parent) {
    if (ts.isJsxExpression(p)) return ts.isJsxElement(p.parent) || ts.isJsxFragment(p.parent);
    if (ts.isParenthesizedExpression(p) || ts.isTemplateSpan(p) || ts.isTemplateExpression(p))
      continue;
    if (ts.isConditionalExpression(p) && hijo !== p.condition) continue;
    if (
      ts.isBinaryExpression(p) &&
      hijo === p.right &&
      [
        ts.SyntaxKind.AmpersandAmpersandToken,
        ts.SyntaxKind.BarBarToken,
        ts.SyntaxKind.QuestionQuestionToken,
      ].includes(p.operatorToken.kind)
    )
      continue;
    return false;
  }
  return false;
}

function textosFijos(): string[] {
  const hallazgos: string[] = [];
  for (const ruta of fuentes(RAIZ)) {
    const fuente = ts.createSourceFile(
      ruta,
      readFileSync(ruta, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const archivo = relative(RAIZ, ruta).split(sep).join("/");
    const anotar = (nodo: ts.Node, texto: string) => {
      const limpio = texto.replace(/\s+/g, " ").trim();
      if (!LETRAS.test(limpio) || EXCEPCIONES.has(limpio)) return;
      const { line } = fuente.getLineAndCharacterOfPosition(nodo.getStart());
      hallazgos.push(`${archivo}:${line + 1} «${limpio}»`);
    };
    const visitar = (nodo: ts.Node) => {
      if (ts.isJsxText(nodo)) anotar(nodo, nodo.text);
      else if (
        ts.isJsxAttribute(nodo) &&
        ATRIBUTOS_VISIBLES.has(nodo.name.getText()) &&
        nodo.initializer &&
        ts.isStringLiteral(nodo.initializer)
      )
        anotar(nodo, nodo.initializer.text);
      else if (
        (ts.isStringLiteral(nodo) || ts.isNoSubstitutionTemplateLiteral(nodo)) &&
        seMuestraEnJsx(nodo)
      )
        anotar(nodo, nodo.text);
      else if (ts.isTemplateExpression(nodo) && seMuestraEnJsx(nodo)) {
        anotar(nodo, nodo.head.text);
        for (const tramo of nodo.templateSpans) anotar(nodo, tramo.literal.text);
      }
      ts.forEachChild(nodo, visitar);
    };
    visitar(fuente);
  }
  return hallazgos;
}

describe("textos del portal", () => {
  it("encuentra las vistas y los componentes", () => {
    const archivos = fuentes(RAIZ).map((r) => relative(RAIZ, r).split(sep).join("/"));
    expect(archivos).toContain("vistas/Consumo.tsx");
    expect(archivos).toContain("componentes/Layout.tsx");
  });

  it("ninguna vista ni componente escribe un texto visible fijo: todo pasa por t()", () => {
    expect(textosFijos()).toEqual([]);
  });

  it("los códigos que conservan el mensaje del backend siguen en el catálogo y sin traducir", () => {
    const catalogo = readFileSync(resolve("../packages/core/src/kallicode_core/errors.py"), "utf8");
    for (const codigo of CON_DETALLE_DEL_BACKEND) {
      expect(catalogo).toContain(`"${codigo}": {`);
      expect(DICT.es[`err.${codigo}`]).toBeUndefined();
    }
  });

  it("cada código de CATALOGO_ERRORES tiene su mensaje en es, en y pt", () => {
    const catalogo = readFileSync(resolve("../packages/core/src/kallicode_core/errors.py"), "utf8");
    const codigos = [...catalogo.matchAll(/^ {4}"([A-Z0-9_]+)": \{/gm)].map((m) => m[1]!);
    expect(codigos.length).toBeGreaterThan(50);
    const faltan = (["es", "en", "pt"] as const).flatMap((idioma) =>
      codigos
        .filter((c) => !CON_DETALLE_DEL_BACKEND.has(c))
        .filter((c) => !DICT[idioma][`err.${c}`] && !DICT[idioma][`red.${c}`])
        .map((c) => `${idioma}: ${c}`),
    );
    expect(faltan).toEqual([]);
  });
});
