/**
 * Convención data-testid `vista.zona.elemento` (KC-PT-04-6), contrato con el equipo de Testing:
 * - tres partes en minúsculas, con guiones dentro de cada parte (`tablero.kpis.en-cola`);
 * - `vista` es el archivo de la vista en kebab-case (`DetalleTicket` → `detalle-ticket`) o,
 *   para los componentes de una vista, su carpeta (`vistas/detalle-ticket/PanelGate.tsx`);
 *   los componentes compartidos usan `layout`, `comun` (ui.tsx) y `error` (LimiteError);
 * - sin ids de datos: las filas repiten el testid y se distinguen por texto o posición.
 */
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { testidsDelContrato } from "./test/contratoSelectores";
import { entrarDesde, renderPortal } from "./test/portal";

const SEGMENTO = "[a-z0-9]+(?:-[a-z0-9]+)*";
const FORMATO = new RegExp(`^${SEGMENTO}\\.${SEGMENTO}\\.${SEGMENTO}$`);

/** Vista de los componentes compartidos; el resto se deduce del nombre del archivo. */
const VISTA_DE_ARCHIVO: Record<string, string> = {
  ui: "comun",
  LimiteError: "error",
  portal: "prueba",
};

const vistaDe = (ruta: string) => {
  // Componentes de una vista (KC-PT-06-7): vistas/<vista>/Componente.tsx.
  const carpeta = /^\.\/vistas\/([a-z0-9-]+)\//.exec(ruta)?.[1];
  if (carpeta) return carpeta;
  const nombre = ruta.split("/").pop()!.replace(".tsx", "");
  return VISTA_DE_ARCHIVO[nombre] ?? nombre.replace(/([a-z])([A-Z])/g, "$1-$2").toLowerCase();
};

const fuentes = import.meta.glob<string>(["./**/*.tsx", "!./**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("convención en el código fuente", () => {
  it("encuentra los archivos de las vistas", () => {
    expect(Object.keys(fuentes)).toContain("./vistas/DetalleTicket.tsx");
  });

  it.each(Object.entries(fuentes))("%s cumple vista.zona.elemento", (ruta, codigo) => {
    const vista = vistaDe(ruta);
    const problemas: string[] = [];

    for (const [, valor] of codigo.matchAll(/data-testid=(\S+?)(?=[\s>])/g)) {
      // Variable de prop (Kpi): se revisa donde se pasa el literal `testid="..."`.
      if (valor === "{testid}") continue;
      const literal = /^"([^"]*)"$/.exec(valor)?.[1] ?? /^\{`([^`]*)`\}$/.exec(valor)?.[1];
      if (literal === undefined) {
        problemas.push(`${valor}: usar "..." o una plantilla \`...\``);
        continue;
      }
      // Las interpolaciones valen como un segmento; la vista se exige solo si es literal.
      const conValores = literal.replace(/\$\{[^}]*\}/g, "x");
      if (!FORMATO.test(conValores)) problemas.push(`${literal}: no es vista.zona.elemento`);
      else if (!literal.startsWith("${") && !literal.startsWith(`${vista}.`))
        problemas.push(`${literal}: la vista debería ser «${vista}»`);
    }
    for (const [, literal] of codigo.matchAll(/(?<![\w-])testid="([^"]*)"/g)) {
      if (!FORMATO.test(literal!)) problemas.push(`${literal}: no es vista.zona.elemento`);
      else if (!literal!.startsWith(`${vista}.`))
        problemas.push(`${literal}: la vista debería ser «${vista}»`);
    }
    // <Paginacion vista="..."> arma `<vista>.paginacion.<elemento>`.
    for (const [, literal] of codigo.matchAll(/\bvista="([^"]*)"/g)) {
      if (literal !== vista) problemas.push(`vista="${literal}": debería ser «${vista}»`);
    }

    expect(problemas).toEqual([]);
  });
});

/** Todos los data-testid presentes en el documento. */
const testidsEnPantalla = () =>
  [...document.querySelectorAll("[data-testid]")].map((el) => el.getAttribute("data-testid")!);

const delContrato = testidsDelContrato();

/**
 * Testids en pantalla que no cumplen el formato o no están en el contrato con Testing
 * (KC-PT-05-5). Cubre los que el código arma con plantillas, que el test estático no expande.
 */
const incumplidos = () =>
  testidsEnPantalla()
    .filter((id) => id !== "prueba.ruta.actual")
    .flatMap((id) => [
      ...(FORMATO.test(id) ? [] : [`${id}: no es vista.zona.elemento`]),
      ...(delContrato.has(id) ? [] : [`${id}: no está en contratos/selectores-portal-v1.json`]),
    ]);

describe("convención en las vistas renderizadas", () => {
  it("login", async () => {
    renderPortal("/login");

    for (const id of ["correo", "clave", "entrar"]) {
      expect(await screen.findByTestId(`login.formulario.${id}`)).toBeInTheDocument();
    }
    expect(incumplidos()).toEqual([]);
  });

  // Selectores mínimos que cada vista expone con los datos de los dobles MSW.
  it.each([
    [
      "/",
      [
        "tablero.vista.raiz",
        "tablero.cabecera.nuevo-ticket",
        "tablero.kpis.en-cola",
        "tablero.lineas.tabla",
        "tablero.kanban.col-triage",
        "layout.nav.tablero",
        "layout.cabecera.salir",
      ],
    ],
    [
      "/tickets",
      [
        "tickets.vista.raiz",
        "tickets.filtros.busqueda",
        "tickets.filtros.etapa",
        "tickets.lista.tabla",
        "tickets.lista.fila",
      ],
    ],
    [
      "/tickets/KC-1001",
      [
        "detalle-ticket.vista.raiz",
        "detalle-ticket.pipeline.etapa-triage",
        "detalle-ticket.pestanas.spec",
        "detalle-ticket.pestanas.actividad",
        "detalle-ticket.firmas.cadena",
        "detalle-ticket.firmas.gate",
      ],
    ],
    [
      "/nuevo-ticket",
      [
        "nuevo-ticket.vista.raiz",
        "nuevo-ticket.formulario.titulo",
        "nuevo-ticket.formulario.descripcion",
        "nuevo-ticket.formulario.enviar",
        "nuevo-ticket.adjuntos.zona",
      ],
    ],
    [
      "/nueva-funcionalidad",
      [
        "nueva-funcionalidad.vista.raiz",
        "nueva-funcionalidad.formulario.nombre",
        "nueva-funcionalidad.criterios.dado",
        "nueva-funcionalidad.formulario.enviar",
        "nueva-funcionalidad.adjuntos.zona",
      ],
    ],
    ["/aprobaciones", ["aprobaciones.vista.raiz"]],
    ["/auditoria", ["auditoria.vista.raiz", "auditoria.filtros.busqueda", "auditoria.lista.tabla"]],
    ["/auditoria/KC-1001", ["expediente.vista.raiz", "expediente.firmas.tabla"]],
    ["/conexiones", ["conexiones.vista.raiz", "conexiones.categorias.repositorio"]],
    ["/modelos", ["modelos-ia.vista.raiz", "modelos-ia.asignaciones.tabla"]],
    ["/consumo", ["consumo.vista.raiz", "consumo.kpis.consumidas", "consumo.tickets.tabla"]],
  ])("%s", async (ruta, esperados) => {
    await entrarDesde(ruta);

    for (const id of esperados) {
      expect((await screen.findAllByTestId(id)).length).toBeGreaterThan(0);
    }
    expect(incumplidos()).toEqual([]);
  });

  it("cada pestaña del detalle de ticket", async () => {
    const usuario = await entrarDesde("/tickets/KC-1001");
    const pestanas = within(await screen.findByTestId("detalle-ticket.vista.raiz"));

    for (const zona of ["spec", "evidencia", "seguridad", "consumo", "actividad"]) {
      await usuario.click(pestanas.getByTestId(`detalle-ticket.pestanas.${zona}`));
      const contenido = await screen.findAllByTestId(
        new RegExp(`^detalle-ticket\\.${zona}\\.(contenido|tabla|lista|sin-datos)$`),
      );
      expect(contenido.length).toBeGreaterThan(0);
    }
    expect(incumplidos()).toEqual([]);
  });
});
