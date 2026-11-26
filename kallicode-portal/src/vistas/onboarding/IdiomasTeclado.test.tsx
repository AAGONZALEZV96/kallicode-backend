/**
 * Idiomas y teclado del asistente (KC-PT-07-6), en el portal completo.
 * - Teclado: los formularios en línea toman el foco al abrirse, se cierran con Escape y lo
 *   devuelven a su botón; el asistente se puede completar sin mouse.
 * - Idiomas: en inglés y portugués ningún texto del asistente queda como clave sin traducir
 *   ni en castellano, y los errores del backend salen en el idioma elegido.
 */
import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { DICT } from "../../test/diccionarios";
import { conexiones, organizacion } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const sinId = (e: string) => screen.queryByTestId(`onboarding.${e}`);
const enCategoria = (categoria: string, e: string) =>
  within(id(`categorias.${categoria}`)).getByTestId(`onboarding.${e}`);

async function abrir(paso = 1) {
  const usuario = await entrarDesde(`/onboarding?paso=${paso}`);
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

/** Un valor válido para el campo según su tipo (proveedores.ts). */
function valorPara(campo: HTMLInputElement): string {
  if (campo.type === "url") return "https://servicio.empresa.com";
  if (campo.inputMode === "numeric") return "5432";
  return "valor_valido";
}

describe("teclado", () => {
  it("alta: el foco entra al primer campo; Escape cierra sin guardar y vuelve a «Agregar»", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));

    expect(id("formulario.proveedor")).toHaveFocus();
    await usuario.type(id("formulario.token"), "ghp_1");
    await usuario.keyboard("{Escape}");

    expect(sinId("formulario.token")).not.toBeInTheDocument();
    expect(id("formulario.abrir")).toHaveFocus();
  });

  it("«Cancelar» también devuelve el foco a «Agregar»", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));
    await usuario.click(id("formulario.cancelar"));

    expect(id("formulario.abrir")).toHaveFocus();
  });

  it("alta solo con teclado: Enter abre y envía, el foco va al resultado y luego a «Agregar»", async () => {
    const usuario = await abrir(2);
    enCategoria("tickets", "formulario.abrir").focus();

    await usuario.keyboard("{Enter}");
    expect(id("formulario.proveedor")).toHaveFocus();
    await usuario.tab(); // nombre (opcional)
    // Enter envía; la validación lleva el foco al primer campo que falta, y así hasta el final.
    for (let i = 0; i < 8 && !sinId("formulario.exito"); i++) {
      await usuario.keyboard("{Enter}");
      const activo = document.activeElement;
      if (activo instanceof HTMLInputElement && !activo.value && activo.required) {
        await usuario.keyboard(valorPara(activo));
      } else {
        await screen.findByTestId("onboarding.formulario.exito");
      }
    }

    const exito = id("formulario.exito");
    await waitFor(() => expect(exito).toHaveFocus());
    await usuario.tab();
    expect(id("formulario.webhook")).toHaveFocus();
    await usuario.tab();
    expect(within(exito).getByRole("button")).toHaveFocus();
    await usuario.keyboard("{Enter}");

    expect(enCategoria("tickets", "formulario.abrir")).toHaveFocus();
  });

  it("Escape no cierra el formulario mientras se prueba la conexión", async () => {
    let responder: () => void = () => {};
    server.use(
      http.post(
        "*/api/v1/connections",
        () =>
          new Promise<Response>((r) => {
            responder = () =>
              r(
                HttpResponse.json(
                  { id: "cn_2", estado: "conectada", permisos_verificados: [] },
                  { status: 201 },
                ),
              );
          }),
      ),
    );
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));
    await usuario.type(id("formulario.repo-url"), "https://github.com/a/b");
    await usuario.type(id("formulario.token"), "ghp_1");
    await usuario.click(id("formulario.enviar"));
    await waitFor(() => expect(id("formulario.enviar")).toBeDisabled());

    id("formulario.repo-url").focus();
    await usuario.keyboard("{Escape}");

    expect(id("formulario.token")).toBeInTheDocument();
    responder();
    await screen.findByTestId("onboarding.formulario.exito");
  });

  it("renombrar: el foco entra al nombre; Escape y guardar lo devuelven a «Renombrar»", async () => {
    const usuario = await abrir();
    await usuario.click(id("conexiones.renombrar"));
    expect(id("conexiones.nombre")).toHaveFocus();

    await usuario.keyboard("{Escape}");
    expect(sinId("conexiones.nombre")).not.toBeInTheDocument();
    expect(id("conexiones.renombrar")).toHaveFocus();

    await usuario.keyboard("{Enter}");
    await usuario.keyboard("{Control>}a{/Control}nuevo{Enter}");
    await waitFor(() => expect(id("conexiones.renombrar")).toHaveFocus());
  });

  it("rotar: el foco entra al primer campo y Escape lo devuelve a «Rotar»", async () => {
    const usuario = await abrir();
    await usuario.click(id("conexiones.rotar"));
    expect(id("formulario.repo-url")).toHaveFocus();

    await usuario.keyboard("{Escape}");

    expect(sinId("formulario.repo-url")).not.toBeInTheDocument();
    expect(id("conexiones.rotar")).toHaveFocus();
  });

  it("al eliminar una conexión, el foco va a «Agregar» de su categoría", async () => {
    let eliminada = false;
    server.use(
      http.get("*/api/v1/connections", () =>
        HttpResponse.json(eliminada ? { ...conexiones, conexiones: [] } : conexiones),
      ),
      http.delete("*/api/v1/connections/:id", () => {
        eliminada = true;
        return HttpResponse.json({ eliminada: true });
      }),
    );
    const usuario = await abrir();

    await usuario.click(id("conexiones.eliminar"));
    await usuario.click(id("eliminacion.confirmar"));

    await waitFor(() => expect(sinId("resumen.conexion")).not.toBeInTheDocument());
    await waitFor(() => expect(id("formulario.abrir")).toHaveFocus());
  });

  it("si la eliminación falla, el foco no salta a «Agregar» aunque la lista se refresque", async () => {
    let consultas = 0;
    server.use(
      // Cada consulta trae otra fecha de prueba: la lista cambia, pero la conexión sigue.
      http.get("*/api/v1/connections", () => {
        consultas += 1;
        const [c] = conexiones.conexiones;
        return HttpResponse.json({
          ...conexiones,
          conexiones: [
            { ...c!, ultimo_test: `2026-09-25T12:00:${String(consultas).padStart(2, "0")}Z` },
          ],
        });
      }),
      http.delete("*/api/v1/connections/:id", () =>
        sobreError(409, "CONEXION_EN_USO", "Hay trabajos activos."),
      ),
    );
    const usuario = await abrir();
    await usuario.click(id("conexiones.eliminar"));
    await usuario.click(id("eliminacion.confirmar"));
    await within(id("eliminacion.dialogo")).findByRole("alert");
    await usuario.keyboard("{Escape}");
    expect(id("conexiones.eliminar")).toHaveFocus();

    // «Probar» refresca la lista: el foco tiene que quedarse en «Probar».
    const antes = consultas;
    await usuario.click(id("conexiones.probar"));
    await waitFor(() => expect(consultas).toBeGreaterThan(antes));
    await screen.findByTestId("onboarding.conexiones.resultado");

    expect(id("conexiones.probar")).toHaveFocus();
  });

  it("cada paso se nombra con su título y su estado separados, y las flechas no se leen", async () => {
    await abrir();

    const pasos = screen.getAllByTestId("onboarding.pasos.paso");
    expect(pasos[0]).toHaveAccessibleName("Repositorio Completo");
    expect(pasos[1]).toHaveAccessibleName("Herramientas Opcional");
    expect(id("navegacion.anterior")).toHaveAccessibleName("Anterior");
    expect(id("navegacion.siguiente")).toHaveAccessibleName("Siguiente");
  });
});

// ---- idiomas ----

/** Prefijos de las claves del asistente. */
const PREFIJOS = ["asis.", "con.", "ramas.", "activacion.", "grafo.", "comun.", "campo."];
const CLAVE_CRUDA = new RegExp(`\\b(${PREFIJOS.map((p) => p.slice(0, -1)).join("|")})\\.[a-z_]+`);

/**
 * Textos que se escriben igual en castellano y en el otro idioma. Es una lista cerrada: un
 * texto nuevo copiado del castellano sin traducir no entra aquí y el test lo detecta.
 */
const IGUALES: Record<"en" | "pt", ReadonlySet<string>> = {
  en: new Set([]),
  pt: new Set(["asis.grafo"]),
};
const mismoTexto = (idioma: "en" | "pt", clave: string) =>
  clave.startsWith("con.proveedor.") || IGUALES[idioma].has(clave);

/** Trozos (sin marcadores) de los textos del asistente en castellano. */
function trozosEnCastellano(idioma: "en" | "pt"): string[] {
  return Object.keys(DICT.es)
    .filter((k) => PREFIJOS.some((p) => k.startsWith(p)) && !mismoTexto(idioma, k))
    .flatMap((k) => DICT.es[k]!.split(/\{\w+\}/))
    .map((s) => s.trim())
    .filter((s) => s.length >= 12);
}

/** Texto visible y nombres accesibles del asistente. */
function textoDelAsistente(): string {
  const raiz = id("vista.raiz");
  const atributos = [...raiz.querySelectorAll("[aria-label], [placeholder], [title]")].flatMap(
    (e) => ["aria-label", "placeholder", "title"].map((a) => e.getAttribute(a) ?? ""),
  );
  return [raiz.textContent ?? "", ...atributos].join("\n");
}

async function recorrerAsistente(usuario: UserEvent): Promise<string[]> {
  const textos: string[] = [];
  for (let paso = 0; paso < 5; paso++) {
    await usuario.click(screen.getAllByTestId("onboarding.pasos.paso")[paso]!);
    await waitFor(() =>
      expect(screen.getAllByTestId("onboarding.pasos.paso")[paso]).toHaveAttribute(
        "aria-current",
        "step",
      ),
    );
    textos.push(textoDelAsistente());
    if (paso === 0) {
      // Formularios del paso: alta con validación, rotación y renombre.
      await usuario.click(id("formulario.abrir"));
      await usuario.click(id("formulario.enviar"));
      textos.push(textoDelAsistente());
      await usuario.click(id("formulario.cancelar"));
      await usuario.click(id("conexiones.rotar"));
      textos.push(textoDelAsistente());
      await usuario.keyboard("{Escape}");
      await usuario.click(id("conexiones.renombrar"));
      textos.push(textoDelAsistente());
      await usuario.keyboard("{Escape}");
    }
  }
  return textos;
}

describe.each<"en" | "pt">(["en", "pt"])("en %s", (idioma) => {
  it("ningún texto del asistente queda sin traducir", async () => {
    localStorage.setItem("kc_idioma", idioma);
    server.use(
      http.get("*/api/v1/organization", () =>
        HttpResponse.json({ ...organizacion, fabrica_activa: false }),
      ),
    );
    const usuario = await abrir();

    const textos = await recorrerAsistente(usuario);

    const castellano = trozosEnCastellano(idioma);
    expect(castellano.length).toBeGreaterThan(50);
    for (const texto of textos) {
      expect(texto).not.toMatch(CLAVE_CRUDA);
      expect(castellano.filter((s) => texto.includes(s))).toEqual([]);
    }
    expect(textos[0]).toContain(DICT[idioma]["asis.repositorio.titulo"]);
  });

  it.each([
    ["CONEXION_EN_USO", "Hay trabajos activos usando esta conexión."],
    ["CONEXION_DUPLICADA", "Ya existe una conexión equivalente."],
  ])("el error %s sale traducido", async (codigo, mensajeDelBackend) => {
    localStorage.setItem("kc_idioma", idioma);
    server.use(
      http.delete("*/api/v1/connections/:id", () => sobreError(409, codigo, mensajeDelBackend)),
      http.post("*/api/v1/connections", () => sobreError(409, codigo, mensajeDelBackend)),
    );
    const usuario = await abrir();

    if (codigo === "CONEXION_EN_USO") {
      await usuario.click(id("conexiones.eliminar"));
      await usuario.click(id("eliminacion.confirmar"));
    } else {
      await usuario.click(id("formulario.abrir"));
      await usuario.type(id("formulario.repo-url"), "https://github.com/a/b");
      await usuario.type(id("formulario.token"), "ghp_1");
      await usuario.click(id("formulario.enviar"));
    }

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(DICT[idioma][`err.${codigo}`]!);
    expect(alerta).not.toHaveTextContent(mensajeDelBackend);
  });
});
