/**
 * CRUD de conexiones en los pasos 1 y 2 del asistente (KC-PT-07-2), en el portal completo.
 * Con los dobles por defecto la organización tiene una conexión de GitHub (cn_1).
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../../api/tipos";
import { fechaCorta } from "../../componentes/ui";
import { conexiones, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const sinId = (e: string) => screen.queryByTestId(`onboarding.${e}`);
const seccion = (categoria: string) => within(id(`categorias.${categoria}`));

async function abrir(paso: 1 | 2, rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde(`/onboarding?paso=${paso}`);
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

/** Guarda el cuerpo de cada llamada a `metodo ruta` y responde como el doble. */
function espiar(metodo: "post" | "patch" | "delete", ruta: string) {
  const cuerpos: unknown[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (request.method === metodo.toUpperCase() && new URL(request.url).pathname === ruta) {
      cuerpos.push(metodo === "delete" ? null : await request.clone().json());
    }
  });
  return cuerpos;
}

describe("lista de conexiones", () => {
  it("el paso 1 lista el repositorio con su estado y última prueba, sin el enlace a Conexiones", async () => {
    await abrir(1);

    const fila = id("resumen.conexion");
    expect(fila.textContent).toBe(
      `GitHub · demo/app Conectada · última prueba: ${fechaCorta(conexiones.conexiones[0]!.ultimo_test)}ProbarRenombrarEliminar`,
    );
    expect(sinId("panel.configurar")).not.toBeInTheDocument();
    // Con una sola categoría el título del paso ya la nombra: no repite su h3.
    expect(seccion("repositorio").queryByRole("heading")).not.toBeInTheDocument();
  });

  it("el paso 2 tiene una sección por herramienta, vacías", async () => {
    await abrir(2);

    for (const [categoria, titulo] of [
      ["tickets", "Tickets"],
      ["cicd", "CI/CD"],
      ["database", "Base de datos"],
    ]) {
      expect(seccion(categoria!).getByRole("heading", { level: 3 })).toHaveTextContent(titulo!);
      expect(seccion(categoria!).getByTestId("onboarding.resumen.vacio")).toBeInTheDocument();
    }
    expect(sinId("categorias.repositorio")).not.toBeInTheDocument();
    expect(sinId("panel.configurar")).not.toBeInTheDocument();
  });
});

describe("alta", () => {
  it("valida antes de enviar: marca los campos y lleva el foco al primero", async () => {
    const cuerpos = espiar("post", "/api/v1/connections");
    const usuario = await abrir(1);
    await usuario.click(id("formulario.abrir"));

    await usuario.click(id("formulario.enviar"));

    const errores = screen.getAllByTestId("onboarding.formulario.error");
    expect(errores.map((e) => e.textContent)).toEqual([
      "Este campo es obligatorio.",
      "Este campo es obligatorio.",
    ]);
    expect(id("formulario.repo-url")).toHaveFocus();
    expect(id("formulario.repo-url")).toHaveAttribute("aria-invalid", "true");
    expect(id("formulario.repo-url")).toHaveAccessibleDescription("Este campo es obligatorio.");

    await usuario.type(id("formulario.repo-url"), "http://github.com/a/b");
    await usuario.type(id("formulario.token"), "ghp_1");
    await usuario.click(id("formulario.enviar"));

    expect(id("formulario.error").textContent).toBe("Debe ser una URL que empiece con https://.");
    expect(id("formulario.token")).not.toHaveAttribute("aria-invalid");
    expect(cuerpos).toEqual([]);
  });

  it("crea un repositorio: manda ConexionIn, muestra los permisos y no hay webhook", async () => {
    const cuerpos = espiar("post", "/api/v1/connections");
    const usuario = await abrir(1);
    await usuario.click(id("formulario.abrir"));

    await usuario.selectOptions(id("formulario.proveedor"), "GitLab");
    await usuario.type(id("formulario.nombre"), "app");
    await usuario.type(id("formulario.repo-url"), "https://gitlab.com/a/b");
    await usuario.type(id("formulario.token"), "glpat_1");
    expect(id("formulario.token")).toHaveAttribute("type", "password");
    await usuario.click(id("formulario.enviar"));

    expect(await screen.findByTestId("onboarding.formulario.exito")).toHaveTextContent(
      "✓ Conexión con GitLab creada y verificada. Permisos verificados: lectura.",
    );
    expect(cuerpos).toEqual([
      {
        categoria: "repositorio",
        proveedor: "gitlab",
        nombre: "app",
        config: { repo_url: "https://gitlab.com/a/b" },
        credenciales: { token: "glpat_1", repo_url: "https://gitlab.com/a/b" },
      },
    ]);
    expect(sinId("formulario.webhook")).not.toBeInTheDocument();

    await usuario.click(screen.getByRole("button", { name: "Listo" }));
    expect(id("formulario.abrir")).toBeInTheDocument();
  });

  it("un gestor de tickets muestra la URL de su webhook una sola vez", async () => {
    const usuario = await abrir(2);
    await usuario.click(seccion("tickets").getByTestId("onboarding.formulario.abrir"));

    await usuario.type(id("formulario.url"), "https://x.atlassian.net");
    await usuario.type(id("formulario.proyecto"), "OPS");
    await usuario.type(id("formulario.usuario"), "kc");
    await usuario.type(id("formulario.api-token"), "t");
    await usuario.click(id("formulario.enviar"));

    const webhook = await screen.findByTestId("onboarding.formulario.webhook");
    expect(webhook).toHaveValue(`${import.meta.env.VITE_API_URL}/api/v1/webhooks/jira/wh_prueba`);
    expect(webhook).toHaveAttribute("readonly");
    expect(webhook).toHaveAccessibleName("URL del webhook");
  });

  it("si el backend rechaza la prueba, muestra el error y conserva lo escrito", async () => {
    const usuario = await abrir(2);
    await usuario.click(seccion("database").getByTestId("onboarding.formulario.abrir"));

    await usuario.type(id("formulario.host"), "db.x");
    await usuario.type(id("formulario.puerto"), "5432");
    await usuario.type(id("formulario.base"), "prod");
    await usuario.type(id("formulario.usuario"), "postgres");
    await usuario.type(id("formulario.contrasena"), "x");
    await usuario.click(id("formulario.enviar"));

    expect(await screen.findByRole("alert")).toHaveTextContent("PERMISOS_EXCESIVOS");
    expect(sinId("formulario.exito")).not.toBeInTheDocument();
    expect(id("formulario.host")).toHaveValue("db.x");
  });

  it("cambiar de proveedor vacía los campos y los errores", async () => {
    const usuario = await abrir(2);
    await usuario.click(seccion("cicd").getByTestId("onboarding.formulario.abrir"));
    await usuario.type(id("formulario.repo-url"), "https://github.com/a/b");
    await usuario.click(id("formulario.enviar"));
    expect(id("formulario.error")).toBeInTheDocument();

    // GitLab CI comparte las claves de GitHub Actions: no debe arrastrar la URL ni el token.
    await usuario.selectOptions(id("formulario.proveedor"), "GitLab CI");

    expect(id("formulario.repo-url")).toHaveValue("");
    expect(sinId("formulario.error")).not.toBeInTheDocument();

    await usuario.selectOptions(id("formulario.proveedor"), "Jenkins");
    expect(id("formulario.url")).toHaveValue("");
    expect(sinId("formulario.repo-url")).not.toBeInTheDocument();
  });

  it("cancelar cierra el formulario", async () => {
    const usuario = await abrir(1);
    await usuario.click(id("formulario.abrir"));

    await usuario.click(id("formulario.cancelar"));

    expect(sinId("formulario.proveedor")).not.toBeInTheDocument();
    expect(id("formulario.abrir")).toBeInTheDocument();
  });
});

describe("probar, renombrar y eliminar", () => {
  it("probar muestra si la conexión responde o por qué falló", async () => {
    const usuario = await abrir(1);
    expect(id("conexiones.probar")).toHaveAccessibleName("Probar la conexión con GitHub");

    await usuario.click(id("conexiones.probar"));
    expect((await screen.findByTestId("onboarding.conexiones.resultado")).textContent).toBe(
      "La conexión responde.",
    );

    server.use(
      http.post("*/api/v1/connections/:id/test", () =>
        HttpResponse.json({
          estado: "error",
          detalle: { ok: false, mensaje: "Credenciales rechazadas." },
        }),
      ),
    );
    await usuario.click(id("conexiones.probar"));
    await waitFor(() =>
      expect(id("conexiones.resultado").textContent).toBe(
        "La prueba falló. Credenciales rechazadas.",
      ),
    );
    expect(id("conexiones.resultado")).toHaveAttribute("role", "status");
  });

  it("renombrar manda solo el nombre y no deja guardarlo vacío", async () => {
    const cuerpos = espiar("patch", "/api/v1/connections/cn_1");
    const usuario = await abrir(1);

    await usuario.click(id("conexiones.renombrar"));
    expect(id("conexiones.nombre")).toHaveValue("demo/app");
    await usuario.clear(id("conexiones.nombre"));
    expect(id("conexiones.guardar")).toBeDisabled();
    await usuario.type(id("conexiones.nombre"), " app principal ");
    await usuario.click(id("conexiones.guardar"));

    await waitFor(() => expect(sinId("conexiones.nombre")).not.toBeInTheDocument());
    expect(cuerpos).toEqual([{ nombre: "app principal" }]);
  });

  it("eliminar pide confirmación; cancelar no borra nada", async () => {
    const cuerpos = espiar("delete", "/api/v1/connections/cn_1");
    const usuario = await abrir(1);

    await usuario.click(id("conexiones.eliminar"));
    const dialogo = id("eliminacion.dialogo");
    expect(dialogo).toHaveAccessibleName("Eliminar la conexión con GitHub");
    expect(dialogo).toHaveTextContent("Se borran sus credenciales de la bóveda");
    await usuario.click(id("eliminacion.cancelar"));

    expect(sinId("eliminacion.dialogo")).not.toBeInTheDocument();
    expect(cuerpos).toEqual([]);

    await usuario.click(id("conexiones.eliminar"));
    await usuario.click(id("eliminacion.confirmar"));

    await waitFor(() => expect(sinId("eliminacion.dialogo")).not.toBeInTheDocument());
    expect(cuerpos).toEqual([null]);
  });

  it("si el repositorio está en uso, el error queda en el diálogo", async () => {
    server.use(
      http.delete("*/api/v1/connections/:id", () =>
        sobreError(409, "CONEXION_EN_USO", "Hay trabajos activos."),
      ),
    );
    const usuario = await abrir(1);

    await usuario.click(id("conexiones.eliminar"));
    await usuario.click(id("eliminacion.confirmar"));

    expect(await within(id("eliminacion.dialogo")).findByRole("alert")).toHaveTextContent(
      "CONEXION_EN_USO",
    );
  });

  it("el admin crea, prueba y renombra, pero no elimina", async () => {
    await abrir(1, "admin");

    expect(id("formulario.abrir")).toBeInTheDocument();
    expect(id("conexiones.probar")).toBeInTheDocument();
    expect(id("conexiones.renombrar")).toBeInTheDocument();
    expect(sinId("conexiones.eliminar")).not.toBeInTheDocument();
  });
});
