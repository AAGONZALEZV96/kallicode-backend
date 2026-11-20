/**
 * Paso 3 del asistente: editar y guardar el mapa de ramas (KC-PT-07-3), en el portal
 * completo. Con los dobles por defecto el mapa es feature/* → develop → main.
 */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../../api/tipos";
import { conexiones, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const sinId = (e: string) => screen.queryByTestId(`onboarding.${e}`);
const campo = (c: "feature" | "staging" | "produccion") => id(`ramas.${c}`);
const errores = () => screen.queryAllByTestId("onboarding.ramas.error").map((e) => e.textContent);

async function abrir(rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde("/onboarding?paso=3");
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

/** Un backend con estado: GET /connections devuelve lo último que guardó el PUT. */
function backendConMapa(inicial: Record<string, string> | null) {
  let mapa = inicial;
  const cuerpos: unknown[] = [];
  server.use(
    http.get("*/api/v1/connections", () => HttpResponse.json({ ...conexiones, mapa_ramas: mapa })),
    http.put("*/api/v1/connections/branch-map", async ({ request }) => {
      const cuerpo = (await request.json()) as Record<string, string>;
      cuerpos.push(cuerpo);
      mapa = cuerpo;
      return HttpResponse.json({ mapa_ramas: cuerpo });
    }),
  );
  return cuerpos;
}

describe("mapa de ramas", () => {
  it("con mapa muestra el guardado y lo trae en el formulario, sin el enlace a Conexiones", async () => {
    await abrir();

    expect(id("resumen.ramas").textContent).toBe("feature/* → develop → main");
    expect(campo("feature")).toHaveValue("feature/*");
    expect(campo("staging")).toHaveValue("develop");
    expect(campo("produccion")).toHaveValue("main");
    expect(campo("feature")).toHaveAccessibleName("Ramas de trabajo");
    expect(campo("feature")).toHaveAccessibleDescription(
      "Patrón de las ramas donde la fábrica propone los cambios; * vale por cualquier nombre.",
    );
    expect(id("ramas.exito").textContent).toBe("");
    // Vacía no se ve: sin el estilo de resultado.
    expect(id("ramas.exito")).not.toHaveClass("res");
    expect(sinId("panel.configurar")).not.toBeInTheDocument();
  });

  it("sin mapa propone los valores sugeridos; al guardarlos el paso queda completo", async () => {
    const cuerpos = backendConMapa(null);
    const usuario = await abrir();
    expect(id("resumen.vacio").textContent).toBe(
      "Aún no está configurado. Te proponemos los valores habituales: revísalos y guárdalos.",
    );
    expect(id("panel.estado")).toHaveTextContent("Pendiente");

    await usuario.click(id("ramas.guardar"));

    await waitFor(() => expect(id("ramas.exito").textContent).toBe("✓ Mapa de ramas guardado."));
    expect(cuerpos).toEqual([{ feature: "feature/*", staging: "develop", produccion: "main" }]);
    await waitFor(() => expect(id("panel.estado")).toHaveTextContent("Completo"));
    expect(id("resumen.ramas").textContent).toBe("feature/* → develop → main");
  });

  it("guarda los valores sin espacios alrededor y al volver a editar se borra el aviso", async () => {
    const cuerpos = backendConMapa(conexiones.mapa_ramas);
    const usuario = await abrir();

    await usuario.clear(campo("staging"));
    await usuario.type(campo("staging"), "  qa ");
    await usuario.click(id("ramas.guardar"));

    await waitFor(() => expect(id("ramas.exito").textContent).toBe("✓ Mapa de ramas guardado."));
    expect(cuerpos).toEqual([{ feature: "feature/*", staging: "qa", produccion: "main" }]);
    expect(campo("staging")).toHaveValue("qa");
    expect(id("ramas.exito")).toHaveClass("res pass");
    await waitFor(() => expect(id("resumen.ramas").textContent).toBe("feature/* → qa → main"));

    await usuario.type(campo("produccion"), "-2");
    expect(id("ramas.exito").textContent).toBe("");
  });
});

describe("validación", () => {
  it("valida antes de enviar: marca cada campo y lleva el foco al primero", async () => {
    const cuerpos = backendConMapa(conexiones.mapa_ramas);
    const usuario = await abrir();

    await usuario.clear(campo("feature"));
    await usuario.clear(campo("staging"));
    await usuario.type(campo("staging"), "dev*");
    await usuario.clear(campo("produccion"));
    await usuario.type(campo("produccion"), "mi rama");
    await usuario.click(id("ramas.guardar"));

    expect(errores()).toEqual([
      "Este campo es obligatorio.",
      "Solo el patrón de las ramas de trabajo puede llevar *.",
      "No es un nombre de rama válido: sin espacios ni ~ ^ : ? [ \\, sin «..» y sin / ni . al inicio o al final.",
    ]);
    expect(campo("feature")).toHaveFocus();
    expect(campo("feature")).toHaveAttribute("aria-invalid", "true");
    // La ayuda y el error, en ese orden.
    expect(campo("feature")).toHaveAccessibleDescription(
      "Patrón de las ramas donde la fábrica propone los cambios; * vale por cualquier nombre. Este campo es obligatorio.",
    );
    expect(cuerpos).toEqual([]);
  });

  it("producción igual a staging se marca en producción", async () => {
    const cuerpos = backendConMapa(conexiones.mapa_ramas);
    const usuario = await abrir();

    await usuario.clear(campo("produccion"));
    await usuario.type(campo("produccion"), "develop");
    await usuario.click(id("ramas.guardar"));

    expect(errores()).toEqual(["Debe ser distinta de la rama de staging."]);
    expect(campo("produccion")).toHaveFocus();
    expect(campo("staging")).not.toHaveAttribute("aria-invalid");
    expect(cuerpos).toEqual([]);
  });

  it("los errores no aparecen hasta intentar guardar", async () => {
    const usuario = await abrir();
    await usuario.clear(campo("staging"));
    expect(errores()).toEqual([]);
  });
});

describe("errores del backend", () => {
  it("un 422 sin campos (staging igual a producción) va en producción, sin caja de error", async () => {
    server.use(
      http.put("*/api/v1/connections/branch-map", () =>
        sobreError(422, "VALIDACION_ENTRADA", "Las ramas deben ser distintas."),
      ),
    );
    const usuario = await abrir();

    await usuario.click(id("ramas.guardar"));

    await waitFor(() => expect(errores()).toEqual(["Las ramas deben ser distintas."]));
    expect(campo("produccion")).toHaveFocus();
    expect(screen.queryByRole("alert", { name: /VALIDACION_ENTRADA/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/VALIDACION_ENTRADA/)).not.toBeInTheDocument();
  });

  it("un 422 con campos marca el campo indicado", async () => {
    server.use(
      http.put("*/api/v1/connections/branch-map", () =>
        HttpResponse.json(
          {
            error: {
              codigo: "VALIDACION_ENTRADA",
              mensaje: "Revisa los campos marcados.",
              detalle: {
                campos: [{ campo: "staging", error: "String should have at most 100 characters" }],
              },
            },
          },
          { status: 422 },
        ),
      ),
    );
    const usuario = await abrir();

    await usuario.click(id("ramas.guardar"));

    await waitFor(() => expect(errores()).toEqual(["Puede tener como máximo 100 caracteres."]));
    expect(campo("staging")).toHaveFocus();
    expect(campo("staging")).toHaveAttribute("aria-invalid", "true");
  });

  it("con trabajos en Build o Deploy muestra JOBS_ACTIVOS y se limpia al editar", async () => {
    server.use(
      http.put("*/api/v1/connections/branch-map", () =>
        sobreError(409, "JOBS_ACTIVOS", "Hay trabajos en Build o Deploy."),
      ),
    );
    const usuario = await abrir();

    await usuario.click(id("ramas.guardar"));

    expect(await screen.findByRole("alert")).toHaveTextContent("JOBS_ACTIVOS");
    expect(errores()).toEqual([]);

    await usuario.type(campo("staging"), "-2");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

it("el admin también guarda el mapa", async () => {
  const cuerpos = backendConMapa(conexiones.mapa_ramas);
  const usuario = await abrir("admin");

  await usuario.click(id("ramas.guardar"));

  await waitFor(() => expect(cuerpos).toHaveLength(1));
});
