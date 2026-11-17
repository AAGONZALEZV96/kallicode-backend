/**
 * Asistente de alta (KC-PT-07-1): reanudación, navegación, progreso y foco, en el portal
 * completo. Con los dobles por defecto la organización ya tiene repositorio, ramas,
 * documentos y la fábrica activa (le faltan solo las herramientas, opcionales).
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../../api/tipos";
import { organizacion, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde, rutaActual } from "../../test/portal";

const pasos = () => screen.getAllByTestId("onboarding.pasos.paso");
const actual = () => pasos().findIndex((p) => p.getAttribute("aria-current") === "step");
const titulo = () =>
  within(screen.getByTestId("onboarding.panel.contenido")).getByRole("heading", { level: 2 });
const id = (e: string) => screen.getByTestId(`onboarding.${e}`);

/** Una organización recién creada: sin conexiones, ramas, documentos ni fábrica. */
function organizacionNueva() {
  server.use(
    http.get("*/api/v1/connections", () => HttpResponse.json({ conexiones: [], mapa_ramas: null })),
    http.get("*/api/v1/organization", () =>
      HttpResponse.json({ ...organizacion, fabrica_activa: false }),
    ),
    http.get("*/api/v1/onboarding/codemapping/status", () =>
      HttpResponse.json({
        fase: 4,
        progreso: 1,
        metricas: { definiciones: 0, documentos: 0 },
        curacion_pendiente: 0,
        ultimo_refresh: null,
      }),
    ),
  );
}

async function abrir(ruta = "/onboarding") {
  const usuario = await entrarDesde(ruta);
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

describe("reanudación", () => {
  it("una organización nueva empieza en el paso 1, con 0 de 5", async () => {
    organizacionNueva();
    await abrir();

    expect(actual()).toBe(0);
    expect(titulo()).toHaveTextContent("Repositorio");
    expect(id("progreso.barra")).toHaveAttribute("aria-valuenow", "0");
    expect(id("panel.estado")).toHaveTextContent("Pendiente");
    expect(id("resumen.vacio")).toHaveTextContent("Aún no hay conexiones.");
  });

  it("con todo lo obligatorio hecho, retoma en la activación y muestra lo configurado", async () => {
    await abrir();

    expect(actual()).toBe(4);
    expect(titulo()).toHaveTextContent("Grafo y activación");
    // Las herramientas (opcionales) no están: 4 de 5.
    expect(id("progreso.barra")).toHaveAttribute("aria-valuenow", "4");
    expect(screen.getByText("4 de 5 pasos completos")).toBeInTheDocument();
    expect(id("resumen.grafo")).toHaveTextContent("Grafo de código: 100 %");
    expect(id("resumen.fabrica")).toHaveTextContent("La fábrica está activa.");
    expect(pasos()[1]).toHaveTextContent("Opcional");
    expect(pasos()[0]).toHaveTextContent("Completo");
  });

  it("respeta el paso de la URL", async () => {
    await abrir("/onboarding?paso=3");
    expect(titulo()).toHaveTextContent("Mapa de ramas");
    expect(id("resumen.ramas")).toHaveTextContent("feature/* → develop → main");
  });

  it("un paso inválido en la URL retoma donde corresponde", async () => {
    await abrir("/onboarding?paso=9");
    expect(actual()).toBe(4);
  });
});

describe("navegación", () => {
  it("siguiente y anterior cambian el paso, la URL y llevan el foco al título", async () => {
    organizacionNueva();
    const usuario = await abrir();
    expect(id("navegacion.anterior")).toBeDisabled();

    await usuario.click(id("navegacion.siguiente"));

    await waitFor(() => expect(rutaActual()).toBe("/onboarding?paso=2"));
    expect(titulo()).toHaveTextContent("Herramientas");
    expect(titulo()).toHaveFocus();
    expect(actual()).toBe(1);

    await usuario.click(id("navegacion.anterior"));

    await waitFor(() => expect(rutaActual()).toBe("/onboarding?paso=1"));
    expect(titulo()).toHaveTextContent("Repositorio");
    expect(titulo()).toHaveFocus();
  });

  it("se puede ir a cualquier paso desde la lista; en el último no hay «siguiente»", async () => {
    organizacionNueva();
    const usuario = await abrir();

    await usuario.click(pasos()[4]!);

    await waitFor(() => expect(titulo()).toHaveTextContent("Grafo y activación"));
    expect(titulo()).toHaveFocus();
    expect(screen.queryByTestId("onboarding.navegacion.siguiente")).not.toBeInTheDocument();
    expect(id("resumen.fabrica")).toHaveTextContent("La fábrica aún no está encendida.");
  });

  it("al abrir la vista el foco no salta al título (solo al cambiar de paso)", async () => {
    await abrir();
    expect(titulo()).not.toHaveFocus();
  });

  it("al volver con los datos en caché, el foco tampoco salta al título", async () => {
    const usuario = await abrir();
    await usuario.click(id("panel.configurar"));
    await waitFor(() => expect(rutaActual()).toBe("/conexiones"));

    await usuario.click(screen.getByTestId("layout.nav.onboarding"));

    await screen.findByTestId("onboarding.panel.contenido");
    expect(titulo()).not.toHaveFocus();
  });

  it("«Configurar en Conexiones» lleva a la vista de conexiones", async () => {
    const usuario = await abrir();

    await usuario.click(id("panel.configurar"));

    await waitFor(() => expect(rutaActual()).toBe("/conexiones"));
  });
});

describe("errores y permisos", () => {
  it("si falla el estado del grafo, el asistente funciona y el error sale en sus pasos", async () => {
    server.use(
      http.get("*/api/v1/onboarding/codemapping/status", () =>
        sobreError(503, "DEPENDENCIA_NO_DISPONIBLE", "CodeMapping no responde."),
      ),
    );
    await abrir("/onboarding?paso=4");

    expect(await screen.findByRole("alert")).toHaveTextContent("DEPENDENCIA_NO_DISPONIBLE");
    expect(id("panel.estado")).toHaveTextContent("Pendiente");
  });

  it.each<Rol>(["member", "architect", "approver", "viewer"])(
    "%s no ve el asistente en el menú ni puede abrirlo",
    async (rol) => {
      server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
      await entrarDesde("/onboarding");

      await waitFor(() => expect(rutaActual()).toBe("/"));
      expect(screen.queryByTestId("layout.nav.onboarding")).not.toBeInTheDocument();
    },
  );

  it("el owner lo ve en el menú", async () => {
    await entrarDesde("/");
    expect(await screen.findByTestId("layout.nav.onboarding")).toHaveAttribute(
      "href",
      "/onboarding",
    );
  });
});
