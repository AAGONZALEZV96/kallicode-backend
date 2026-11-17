/**
 * Formulario de bóveda (KC-PT-07-4): máscara de los secretos y rotación de credenciales,
 * en el portal completo. Con los dobles por defecto hay una conexión de GitHub (cn_1).
 */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../../api/tipos";
import { conexiones, perfilOwner } from "../../mocks/datos";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const sinId = (e: string) => screen.queryByTestId(`onboarding.${e}`);

async function abrir(rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde("/onboarding?paso=1");
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

/** Guarda el cuerpo de cada PATCH a cn_1. */
function espiarPatch() {
  const cuerpos: unknown[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (
      request.method === "PATCH" &&
      new URL(request.url).pathname === "/api/v1/connections/cn_1"
    ) {
      cuerpos.push(await request.clone().json());
    }
  });
  return cuerpos;
}

describe("máscara de los secretos", () => {
  it("lo escrito va enmascarado y «Mostrar» lo deja ver mientras está presionado", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));
    await usuario.type(id("formulario.token"), "ghp_secreto");

    const token = id("formulario.token");
    const mostrar = id("formulario.mostrar");
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveAttribute("autocomplete", "new-password");
    expect(mostrar).toHaveAccessibleName("Mostrar Token de acceso (PAT)");
    expect(mostrar).toHaveAttribute("aria-pressed", "false");
    expect(mostrar).toHaveAttribute("aria-controls", token.id);

    await usuario.click(mostrar);
    expect(token).toHaveAttribute("type", "text");
    expect(mostrar).toHaveAttribute("aria-pressed", "true");
    // El texto no cambia (el nombre accesible lo contiene); el estado va en aria-pressed.
    expect(mostrar.textContent).toBe("Mostrar");

    await usuario.click(mostrar);
    expect(token).toHaveAttribute("type", "password");
  });

  it("cambiar de proveedor vuelve a ocultar los secretos", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));
    await usuario.type(id("formulario.token"), "ghp_1");
    await usuario.click(id("formulario.mostrar"));
    expect(id("formulario.token")).toHaveAttribute("type", "text");

    // GitLab también tiene `token`: el mismo campo no debe quedar a la vista.
    await usuario.selectOptions(id("formulario.proveedor"), "GitLab");

    expect(id("formulario.token")).toHaveValue("");
    expect(id("formulario.token")).toHaveAttribute("type", "password");
    expect(id("formulario.mostrar")).toHaveAttribute("aria-pressed", "false");
  });

  it("solo los secretos tienen «Mostrar»: uno por secreto", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));

    // GitHub: repo_url (no secreto) y token.
    expect(screen.getAllByTestId("onboarding.formulario.mostrar")).toHaveLength(1);
    expect(id("formulario.repo-url")).toHaveAttribute("type", "url");
  });

  it("tras crear, lo escrito se olvida: al volver a abrir los campos están vacíos y ocultos", async () => {
    const usuario = await abrir();
    await usuario.click(id("formulario.abrir"));
    await usuario.type(id("formulario.repo-url"), "https://github.com/a/b");
    await usuario.type(id("formulario.token"), "ghp_1");
    await usuario.click(id("formulario.mostrar"));
    await usuario.click(id("formulario.enviar"));
    await screen.findByTestId("onboarding.formulario.exito");
    expect(screen.queryByDisplayValue("ghp_1")).not.toBeInTheDocument();

    await usuario.click(screen.getByRole("button", { name: "Listo" }));
    await usuario.click(id("formulario.abrir"));

    expect(id("formulario.token")).toHaveValue("");
    expect(id("formulario.token")).toHaveAttribute("type", "password");
    expect(id("formulario.mostrar")).toHaveAttribute("aria-pressed", "false");
  });
});

describe("rotar credenciales", () => {
  it("pide todos los datos con los secretos guardados enmascarados, sin proveedor ni nombre", async () => {
    const usuario = await abrir();
    expect(id("conexiones.rotar")).toHaveAccessibleName("Rotar las credenciales de GitHub");

    await usuario.click(id("conexiones.rotar"));

    expect(
      screen.getByText(
        "Por seguridad no mostramos lo guardado: escribe de nuevo los datos de GitHub. Si la prueba falla, se mantienen los actuales.",
      ),
    ).toBeInTheDocument();
    expect(id("formulario.token")).toHaveValue("");
    expect(id("formulario.token")).toHaveAttribute(
      "placeholder",
      "•••••••• (guardado en la bóveda)",
    );
    expect(id("formulario.repo-url")).toHaveAttribute(
      "placeholder",
      "https://github.com/empresa/app",
    );
    expect(sinId("formulario.proveedor")).not.toBeInTheDocument();
    expect(sinId("formulario.nombre")).not.toBeInTheDocument();
    expect(id("formulario.enviar").textContent).toBe("Probar y guardar");
  });

  it("valida, manda config y credenciales al PATCH y confirma; al terminar el foco vuelve a «Rotar»", async () => {
    const cuerpos = espiarPatch();
    const usuario = await abrir();
    await usuario.click(id("conexiones.rotar"));

    await usuario.click(id("formulario.enviar"));
    expect(screen.getAllByTestId("onboarding.formulario.error")).toHaveLength(2);
    expect(cuerpos).toEqual([]);

    await usuario.type(id("formulario.repo-url"), "https://github.com/demo/app");
    await usuario.type(id("formulario.token"), "ghp_nuevo");
    await usuario.click(id("formulario.enviar"));

    expect((await screen.findByTestId("onboarding.formulario.exito")).textContent).toBe(
      "✓ Credenciales de GitHub actualizadas y verificadas.Listo",
    );
    expect(cuerpos).toEqual([
      {
        config: { repo_url: "https://github.com/demo/app" },
        credenciales: { token: "ghp_nuevo", repo_url: "https://github.com/demo/app" },
      },
    ]);
    expect(sinId("formulario.webhook")).not.toBeInTheDocument();

    await usuario.click(screen.getByRole("button", { name: "Listo" }));

    await waitFor(() => expect(id("conexiones.rotar")).toHaveFocus());
  });

  it("si la prueba falla, avisa que se mantienen las anteriores y conserva lo escrito", async () => {
    const usuario = await abrir();
    await usuario.click(id("conexiones.rotar"));
    await usuario.type(id("formulario.repo-url"), "https://github.com/demo/app");
    await usuario.type(id("formulario.token"), "invalido");

    await usuario.click(id("formulario.enviar"));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("CREDENCIALES_INVALIDAS");
    expect(alerta).toHaveTextContent("Se mantienen las anteriores.");
    expect(sinId("formulario.exito")).not.toBeInTheDocument();
    expect(id("formulario.repo-url")).toHaveValue("https://github.com/demo/app");
  });

  it("cancelar no manda nada y devuelve el foco a «Rotar»", async () => {
    const cuerpos = espiarPatch();
    const usuario = await abrir();
    await usuario.click(id("conexiones.rotar"));

    await usuario.click(id("formulario.cancelar"));

    expect(sinId("formulario.token")).not.toBeInTheDocument();
    expect(id("conexiones.rotar")).toHaveFocus();
    expect(cuerpos).toEqual([]);
  });

  it("el admin también rota", async () => {
    await abrir("admin");
    expect(id("conexiones.rotar")).toBeInTheDocument();
  });

  it("un proveedor fuera del catálogo del portal no se rota desde el asistente", async () => {
    server.use(
      http.get("*/api/v1/connections", () =>
        HttpResponse.json({
          ...conexiones,
          conexiones: [{ ...conexiones.conexiones[0]!, proveedor: "azure_repos" }],
        }),
      ),
    );
    const usuario = await abrir();

    await usuario.click(id("conexiones.rotar"));

    expect(
      screen.getByText(
        "Este proveedor no se puede rotar desde el asistente: hazlo en la vista Conexiones.",
      ),
    ).toBeInTheDocument();
    expect(sinId("formulario.enviar")).not.toBeInTheDocument();
  });
});
