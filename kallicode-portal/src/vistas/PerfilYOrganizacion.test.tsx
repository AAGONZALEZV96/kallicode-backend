/**
 * Perfil propio y ajustes de la organización (KC-PT-10-4), en el portal completo.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { PASSWORD_DEMO, perfilOwner } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

const NUEVA = "Tq8vLm2Rx9pWz4nB";
const id = (testid: string) => screen.getByTestId(testid);

/** Peticiones a una ruta: método, cuerpo y cabecera If-Match. */
function pedidas(ruta: string) {
  const lista: { metodo: string; cuerpo?: unknown; ifMatch: string | null }[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (new URL(request.url).pathname !== ruta) return;
    const cuerpo = request.method === "GET" ? undefined : await request.clone().json();
    lista.push({ metodo: request.method, cuerpo, ifMatch: request.headers.get("If-Match") });
  });
  return lista;
}
const conRol = (rol: Rol) =>
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));

describe("perfil propio", () => {
  async function abrir() {
    const usuario = await entrarDesde("/");
    await usuario.click(await screen.findByTestId("layout.nav.perfil"));
    await screen.findByTestId("perfil.vista.raiz");
    return usuario;
  }

  it("muestra el correo y el rol sin editarlos; sin cambios no se guarda", async () => {
    await abrir();

    expect(id("perfil.datos.correo")).toHaveTextContent(perfilOwner.email);
    expect(id("perfil.datos.rol")).toHaveTextContent("Propietario");
    expect(id("perfil.datos.nombre")).toHaveValue(perfilOwner.nombre);
    expect(id("perfil.datos.guardar")).toBeDisabled();
  });

  it("guarda solo lo que cambió; el nombre y el idioma nuevos se ven en todo el portal", async () => {
    const enviadas = pedidas("/api/v1/users/me");
    const usuario = await abrir();

    await usuario.clear(id("perfil.datos.nombre"));
    await usuario.type(id("perfil.datos.nombre"), "  Olga Nueva ");
    await usuario.selectOptions(id("perfil.datos.idioma"), "en");
    await usuario.click(id("perfil.datos.guardar"));

    expect(await screen.findByTestId("perfil.datos.guardado")).toHaveTextContent("Changes saved.");
    expect(enviadas.filter((p) => p.metodo === "PATCH").map((p) => p.cuerpo)).toEqual([
      { nombre: "Olga Nueva", idioma: "en" },
    ]);
    // El portal pasa al idioma de la cuenta y la cabecera muestra el nombre nuevo.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("My profile");
    expect(id("layout.cabecera.avatar")).toHaveAccessibleName("Olga Nueva");
    expect(id("perfil.datos.guardar")).toBeDisabled();
  });

  it("un nombre muy corto se marca sin enviar", async () => {
    const enviadas = pedidas("/api/v1/users/me");
    const usuario = await abrir();
    await usuario.clear(id("perfil.datos.nombre"));
    await usuario.type(id("perfil.datos.nombre"), "O");
    await usuario.click(id("perfil.datos.guardar"));

    expect(id("perfil.datos.error-nombre")).toHaveTextContent("Debe tener al menos 2 caracteres.");
    expect(id("perfil.datos.nombre")).toHaveFocus();
    expect(enviadas.filter((p) => p.metodo === "PATCH")).toEqual([]);
  });

  async function cambiarClave(
    usuario: Awaited<ReturnType<typeof abrir>>,
    actual: string,
    nueva = NUEVA,
    otra = nueva,
  ) {
    if (actual) await usuario.type(id("perfil.clave.actual"), actual);
    await usuario.type(id("perfil.clave.nueva"), nueva);
    await usuario.type(id("perfil.clave.confirmar"), otra);
    await usuario.click(id("perfil.clave.cambiar"));
  }

  it("cambiar la contraseña cierra la sesión y vuelve al login con el aviso", async () => {
    const enviadas = pedidas("/api/v1/users/me");
    const salidas = pedidas("/api/v1/auth/logout");
    const usuario = await abrir();
    expect(id("perfil.clave.formulario")).toHaveTextContent("se cierran todas tus sesiones");

    await cambiarClave(usuario, PASSWORD_DEMO);

    await waitFor(() => expect(rutaActual()).toBe("/login"));
    expect(id("login.avisos.mensaje")).toHaveTextContent(
      "Contraseña cambiada. Inicia sesión con tu nueva contraseña.",
    );
    expect(enviadas.filter((p) => p.metodo === "PATCH").map((p) => p.cuerpo)).toEqual([
      { password_actual: PASSWORD_DEMO, password_nueva: NUEVA },
    ]);
    await waitFor(() => expect(salidas).toHaveLength(1));
  });

  it("con la contraseña actual equivocada, el error va en ese campo", async () => {
    const usuario = await abrir();
    await cambiarClave(usuario, "otra-cosa");

    expect(await screen.findByTestId("perfil.clave.error-actual")).toHaveTextContent(
      "La contraseña actual no es correcta.",
    );
    expect(id("perfil.clave.actual")).toHaveAttribute("aria-invalid", "true");
    expect(rutaActual()).toBe("/perfil");
    // Al corregirla, el error del servidor se va.
    await usuario.type(id("perfil.clave.actual"), "x");
    expect(screen.queryByTestId("perfil.clave.error-actual")).toBeNull();
  });

  it.each([
    ["sin la actual", "", NUEVA, NUEVA, "actual", "Este campo es obligatorio."],
    [
      "una nueva sin mayúscula",
      PASSWORD_DEMO,
      "minusculas123",
      "minusculas123",
      "nueva",
      "no cumple",
    ],
    ["contraseñas distintas", PASSWORD_DEMO, NUEVA, NUEVA + "x", "confirmar", "no coinciden"],
  ])("%s: error junto al campo y sin enviar", async (_, actual, nueva, otra, campo, texto) => {
    const enviadas = pedidas("/api/v1/users/me");
    const usuario = await abrir();
    await cambiarClave(usuario, actual, nueva, otra);

    expect(id(`perfil.clave.error-${campo}`)).toHaveTextContent(texto);
    expect(id(`perfil.clave.${campo}`)).toHaveFocus();
    expect(enviadas.filter((p) => p.metodo === "PATCH")).toEqual([]);
  });
});

describe("ajustes de la organización", () => {
  async function abrir() {
    const usuario = await entrarDesde("/");
    await usuario.click(await screen.findByTestId("layout.nav.organizacion"));
    await screen.findByTestId("organizacion.vista.raiz");
    return usuario;
  }

  it("los datos vigentes, con el plan, el despliegue y la fábrica sin editar", async () => {
    await abrir();

    expect(id("organizacion.formulario.nombre")).toHaveValue("Demo Mobility");
    expect(id("organizacion.formulario.zona")).toHaveValue("Europe/Madrid");
    const resumen = id("organizacion.datos.resumen");
    expect(resumen).toHaveTextContent("Growth");
    expect(resumen).toHaveTextContent("Encendida");
    expect(within(resumen).getByTestId("organizacion.datos.plan")).toHaveAttribute(
      "href",
      "/consumo",
    );
    expect(id("organizacion.formulario.guardar")).toBeDisabled();
  });

  it("guarda solo lo que cambió, con el ETag como If-Match", async () => {
    const enviadas = pedidas("/api/v1/organization");
    const usuario = await abrir();

    await usuario.clear(id("organizacion.formulario.nombre"));
    await usuario.type(id("organizacion.formulario.nombre"), "Demo Mobility SpA");
    await usuario.selectOptions(id("organizacion.formulario.zona"), "America/Santiago");
    await usuario.click(id("organizacion.formulario.guardar"));

    expect(await screen.findByTestId("organizacion.avisos.guardado")).toHaveTextContent(
      "Cambios guardados.",
    );
    expect(enviadas.filter((p) => p.metodo === "PATCH")).toEqual([
      {
        metodo: "PATCH",
        cuerpo: { nombre: "Demo Mobility SpA", timezone: "America/Santiago" },
        ifMatch: 'W/"1"',
      },
    ]);
  });

  it("si otra persona la cambió (412): lo avisa, recarga y no pisa sus cambios", async () => {
    const enviadas = pedidas("/api/v1/organization");
    let patch = 0;
    server.use(
      http.patch("*/api/v1/organization", () => {
        patch++;
        return sobreError(412, "PRECONDICION_FALLIDA", "El recurso cambió.");
      }),
      // Lo que guardó la otra persona.
      http.get("*/api/v1/organization", () =>
        HttpResponse.json(
          {
            id: "org_demo",
            nombre: "Nombre de la otra persona",
            idioma: "es",
            timezone: "Europe/Madrid",
            despliegue: "cloud",
            fabrica_activa: true,
            plan: null,
          },
          { headers: { ETag: patch ? 'W/"2"' : 'W/"1"' } },
        ),
      ),
    );
    const usuario = await abrir();
    await usuario.clear(id("organizacion.formulario.nombre"));
    await usuario.type(id("organizacion.formulario.nombre"), "Mi cambio");
    await usuario.click(id("organizacion.formulario.guardar"));

    expect(await screen.findByTestId("organizacion.avisos.conflicto")).toHaveTextContent(
      "Otra persona cambió los ajustes mientras editabas.",
    );
    await waitFor(() =>
      expect(id("organizacion.formulario.nombre")).toHaveValue("Nombre de la otra persona"),
    );
    expect(enviadas.filter((p) => p.metodo === "GET").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByTestId("organizacion.avisos.guardado")).toBeNull();
    // Un despliegue conocido, traducido.
    expect(id("organizacion.datos.resumen")).toHaveTextContent("Nube");
  });

  it.each<Rol>(["architect", "member"])(
    "un %s no la ve en el menú ni puede abrirla",
    async (rol) => {
      conRol(rol);
      await entrarDesde("/organizacion");

      await waitFor(() => expect(rutaActual()).not.toBe("/organizacion"));
      expect(screen.queryByTestId("layout.nav.organizacion")).toBeNull();
      expect(screen.getByTestId("layout.nav.perfil")).toBeInTheDocument();
    },
  );
});
