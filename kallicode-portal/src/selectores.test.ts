/**
 * Congelación de selectores data-testid (KC-PT-05-5): el contrato con Testing es
 * contratos/selectores-portal-v1.json. Esta suite falla si el código agrega, renombra o
 * quita un selector sin actualizar el contrato, y mantiene al día la matriz de interacción
 * (docs/matriz-interaccion.md, se regenera con `npm run selectores:doc`).
 */
import { describe, expect, it } from "vitest";

import {
  leerContrato,
  RUTA_CONTRATO,
  testidsDelContrato,
  type ContratoSelectores,
} from "./test/contratoSelectores";

const contrato = leerContrato();
const delContrato = testidsDelContrato(contrato);

const fuentes = import.meta.glob<string>(["./**/*.tsx", "!./**/*.test.tsx", "!./test/**"], {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Los testid escritos tal cual y las plantillas (`${vista}.paginacion.siguiente`) del código. */
const literales = new Set<string>();
const plantillas: { plantilla: string; patron: RegExp }[] = [];
for (const codigo of Object.values(fuentes)) {
  for (const [, literal, plantilla] of codigo.matchAll(
    /(?:data-testid|(?<![\w-])testid)=(?:"([^"]+)"|\{`([^`]+)`\})/g,
  )) {
    if (literal) literales.add(literal);
    else if (plantilla) {
      const partes = plantilla.split(/\$\{[^}]*\}/).map((p) => p.replace(/[.-]/g, "\\$&"));
      plantillas.push({ plantilla, patron: new RegExp(`^${partes.join("[a-z0-9-]+")}$`) });
    }
  }
}

describe("contrato de selectores", () => {
  it("encuentra el código de las vistas", () => {
    expect(literales.size).toBeGreaterThan(100);
    expect(plantillas.length).toBeGreaterThan(10);
  });

  it("cada selector es único, cumple vista.zona.elemento y pertenece a su vista", () => {
    const formato = new RegExp(contrato.formato);
    const todos = contrato.vistas.flatMap((v) => v.selectores.map((s) => [v.vista, s.testid]));
    const problemas = todos.flatMap(([vista, testid]) => [
      ...(formato.test(testid!) ? [] : [`${testid}: no es vista.zona.elemento`]),
      ...(testid!.startsWith(`${vista}.`) ? [] : [`${testid}: está en la vista «${vista}»`]),
    ]);

    expect(problemas).toEqual([]);
    expect(delContrato.size).toBe(todos.length);
  });

  it("cada data-testid escrito en el código está en el contrato", () => {
    expect([...literales].filter((id) => !delContrato.has(id))).toEqual([]);
  });

  it("cada plantilla del código tiene sus selectores en el contrato", () => {
    const sinSelectores = plantillas
      .filter(({ patron }) => ![...delContrato].some((id) => patron.test(id)))
      .map(({ plantilla }) => plantilla);

    expect(sinSelectores).toEqual([]);
  });

  it("cada selector del contrato sigue existiendo en el código", () => {
    const huerfanos = [...delContrato].filter(
      (id) => !literales.has(id) && !plantillas.some(({ patron }) => patron.test(id)),
    );

    expect(huerfanos).toEqual([]);
  });

  it("la matriz de interacción está al día con el contrato", async () => {
    await expect(generarMatriz(contrato)).toMatchFileSnapshot("../docs/matriz-interaccion.md");
  });
});

const ACCIONES: Record<string, string> = {
  click: "Hacer clic",
  escribir: "Escribir texto",
  seleccionar: "Elegir una opción",
  subir: "Adjuntar archivos (setInputFiles)",
  soltar: "Soltar archivos (drag & drop)",
  verificar: "Solo verificar (visible, texto, estado)",
};

const celda = (texto = "") => texto.replace(/\|/g, "\\|");

/** Matriz de interacción legible para Testing, generada desde el contrato. */
function generarMatriz({ vistas, version }: ContratoSelectores): string {
  const total = vistas.reduce((n, v) => n + v.selectores.length, 0);
  const roles = (r: string[]) => (r.length ? r.join(", ") : "sin sesión");
  const lineas = [
    `# Matriz de interacción del portal (selectores v${version})`,
    "",
    `> Generado desde \`${RUTA_CONTRATO.replace("../", "")}\` por \`src/selectores.test.ts\`. No se edita a mano: se cambia el JSON y se corre \`npm run selectores:doc\`.`,
    "",
    `Contrato de selectores \`data-testid\` del portal con el equipo de Testing (KC-PT-05-5): **${total} selectores en ${vistas.length} vistas**.`,
    "",
    "## Cómo usarlo",
    "",
    '- Cada selector es un `data-testid` con la convención `vista.zona.elemento`: `page.getByTestId("tickets.filtros.busqueda")` en Playwright.',
    "- `vista` es la pantalla (el archivo de la vista en kebab-case); `layout`, `comun` y `error` son compartidos y aparecen dentro de cualquier vista: conviene buscarlos dentro de `<vista>.vista.raiz`.",
    "- Los selectores marcados con 🔁 se repiten una vez por fila o ítem: se distinguen por texto (`.filter({ hasText })`) o posición (`.nth(i)`). No llevan ids de datos.",
    "- **Tipo** usa los mismos valores que `SelectorKind` de `packages/testing` (inventory_client.py): button, input, table, card, link y modal.",
    "- **Cuándo aparece** indica el rol, el estado o los datos que necesita el selector; si está vacío, aparece siempre que la vista cargó.",
    "",
    "| Acción | Qué hacer |",
    "| --- | --- |",
    ...Object.entries(ACCIONES).map(([a, texto]) => `| ${a} | ${texto} |`),
    "",
    "## Reglas de cambio",
    "",
    `- La versión ${version} está congelada: un selector no se renombra ni se quita sin anunciarlo en \`contratos/cambios-anunciados.yaml\` (\`breaking: true\`, con aprobación de Testing) y publicar una versión nueva del contrato.`,
    "- Agregar selectores no rompe a nadie: se agregan al JSON y se anuncian con `breaking: false`.",
    "- El test `src/selectores.test.ts` del portal falla si el código y el contrato no coinciden, en cualquiera de los dos sentidos.",
    "",
    "## Vistas",
    "",
    "| Vista | Ruta | Roles | Selectores |",
    "| --- | --- | --- | --- |",
    ...vistas.map(
      (v) =>
        `| [${v.vista}](#${v.vista}) | \`${v.ruta}\` | ${roles(v.roles)} | ${v.selectores.length} |`,
    ),
  ];
  for (const v of vistas) {
    lineas.push(
      "",
      `## ${v.vista}`,
      "",
      `${v.descripcion} Ruta \`${v.ruta}\` · roles: ${roles(v.roles)}.`,
      "",
      "| data-testid | Tipo | Acción | Qué hace o qué verificar | Cuándo aparece |",
      "| --- | --- | --- | --- | --- |",
      ...v.selectores.map(
        (s) =>
          `| \`${s.testid}\`${s.repetido ? " 🔁" : ""} | ${s.tipo} | ${s.accion} | ${celda(s.descripcion)} | ${celda(s.condicion)} |`,
      ),
    );
  }
  return lineas.join("\n") + "\n";
}
