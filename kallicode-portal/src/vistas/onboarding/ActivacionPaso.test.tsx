/**
 * Paso 5 del asistente: avance del grafo y encendido de la fábrica (KC-PT-07-5), en el
 * portal completo. Con los dobles por defecto hay repositorio y mapa de ramas, el grafo
 * está al 100 % y la fábrica activa.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { INTERVALO_GRAFO_MS, intervaloDelGrafo } from "../../api/consultas";
import type { EstadoGrafo, Rol } from "../../api/tipos";
import { conexiones, estadoGrafo, organizacion, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde, rutaActual } from "../../test/portal";

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const sinId = (e: string) => screen.queryByTestId(`onboarding.${e}`);
const titulo = () => within(id("panel.contenido")).getByRole("heading", { level: 2 });

async function abrir(rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde("/onboarding?paso=5");
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

/** Fábrica apagada; el POST la enciende y la organización lo refleja al refrescarse. */
function fabricaApagada(lineas = 3) {
  let activa = false;
  const llamadas: string[] = [];
  server.use(
    http.get("*/api/v1/organization", () =>
      HttpResponse.json({ ...organizacion, fabrica_activa: activa }),
    ),
    http.post("*/api/v1/onboarding/complete", () => {
      llamadas.push("POST");
      activa = true;
      return HttpResponse.json({ activado: true, lineas_activadas: lineas, pendientes: [] });
    }),
  );
  return llamadas;
}

/** Sin repositorio conectado y, si se pide, sin mapa de ramas. */
function faltan({ ramas }: { ramas: boolean }) {
  server.use(
    http.get("*/api/v1/connections", () =>
      HttpResponse.json({
        conexiones: [],
        mapa_ramas: ramas ? null : conexiones.mapa_ramas,
      }),
    ),
  );
}

describe("grafo", () => {
  it("al 100 %: barra, fase y métricas, sin el aviso de que sigue construyéndose", async () => {
    await abrir();

    expect(id("resumen.grafo").textContent).toBe("Grafo de código: 100 %");
    expect(id("grafo.barra")).toHaveAttribute("aria-valuenow", "100");
    expect(id("grafo.barra")).toHaveAccessibleName("Grafo de código: 100 %");
    expect(id("grafo.detalle").textContent).toBe("Fase 7 de 7 · 1240 definiciones · 3 documentos");
    expect(screen.queryByText(/se sigue construyendo/)).not.toBeInTheDocument();
  });

  it("en proceso: avance parcial, fecha de actualización y curación pendiente", async () => {
    const enProceso: EstadoGrafo = {
      fase: 4,
      progreso: 0.42,
      metricas: { definiciones: 80, documentos: 2 },
      curacion_pendiente: 5,
      ultimo_refresh: "2026-11-16T10:00:00Z",
    };
    server.use(
      http.get("*/api/v1/onboarding/codemapping/status", () => HttpResponse.json(enProceso)),
    );
    await abrir();

    expect(id("grafo.barra")).toHaveAttribute("aria-valuenow", "42");
    expect(id("grafo.detalle").textContent).toMatch(
      /^Fase 4 de 7 · 80 definiciones · 2 documentos · actualizado: \S/,
    );
    expect(
      screen.getByText("El grafo se sigue construyendo; este avance se actualiza solo."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("5 elementos esperan revisión en la curación del grafo."),
    ).toBeInTheDocument();
  });

  it("se vuelve a consultar cada 15 s solo mientras no llega al 100 %", () => {
    expect(INTERVALO_GRAFO_MS).toBe(15_000);
    expect(intervaloDelGrafo({ ...estadoGrafo, progreso: 0.99 })).toBe(15_000);
    expect(intervaloDelGrafo({ ...estadoGrafo, progreso: 1 })).toBe(false);
    expect(intervaloDelGrafo(undefined)).toBe(false);
  });
});

describe("fábrica activa", () => {
  it("lo dice y ofrece ir al tablero, sin botón de encender", async () => {
    const usuario = await abrir();

    expect(id("resumen.fabrica").textContent).toBe("La fábrica está activa.");
    expect(sinId("activacion.encender")).not.toBeInTheDocument();
    expect(id("activacion.resultado").textContent).toBe("");
    expect(sinId("panel.configurar")).not.toBeInTheDocument();

    await usuario.click(id("activacion.tablero"));
    await waitFor(() => expect(rutaActual()).toBe("/"));
  });
});

describe("encender", () => {
  it("pide confirmación, enciende, anuncia las líneas y lleva el foco al tablero", async () => {
    const llamadas = fabricaApagada();
    const usuario = await abrir();
    expect(id("resumen.fabrica").textContent).toBe("La fábrica aún no está encendida.");
    expect(id("activacion.encender")).toHaveAttribute("aria-disabled", "false");
    expect(sinId("activacion.pendientes")).not.toBeInTheDocument();

    await usuario.click(id("activacion.encender"));
    const dialogo = id("encendido.dialogo");
    expect(dialogo).toHaveAccessibleName("¿Encender la fábrica?");
    expect(dialogo).toHaveTextContent("Se crean las líneas de producción de tu plan");
    await usuario.click(id("encendido.confirmar"));

    await waitFor(() =>
      expect(id("activacion.resultado").textContent).toBe(
        "✓ Fábrica encendida con 3 líneas de producción.",
      ),
    );
    expect(sinId("encendido.dialogo")).not.toBeInTheDocument();
    expect(llamadas).toEqual(["POST"]);
    await waitFor(() => expect(id("activacion.tablero")).toHaveFocus());
    expect(id("resumen.fabrica").textContent).toBe("La fábrica está activa.");
    expect(id("panel.estado")).toHaveTextContent("Completo");
  });

  it("con una sola línea lo dice en singular", async () => {
    fabricaApagada(1);
    const usuario = await abrir();

    await usuario.click(id("activacion.encender"));
    await usuario.click(id("encendido.confirmar"));

    await waitFor(() =>
      expect(id("activacion.resultado").textContent).toBe(
        "✓ Fábrica encendida con 1 línea de producción.",
      ),
    );
  });

  it("cancelar cierra la confirmación sin encender", async () => {
    const llamadas = fabricaApagada();
    const usuario = await abrir();

    await usuario.click(id("activacion.encender"));
    await usuario.click(id("encendido.cancelar"));

    expect(sinId("encendido.dialogo")).not.toBeInTheDocument();
    expect(llamadas).toEqual([]);
    expect(id("resumen.fabrica").textContent).toBe("La fábrica aún no está encendida.");
  });

  it("si el backend dice ONBOARDING_INCOMPLETO, el error queda en el diálogo y se refrescan los datos", async () => {
    fabricaApagada();
    let consultas = 0;
    server.use(
      http.get("*/api/v1/connections", () => {
        consultas += 1;
        return HttpResponse.json(conexiones);
      }),
      http.post("*/api/v1/onboarding/complete", () =>
        sobreError(409, "ONBOARDING_INCOMPLETO", "Faltan pasos para encender la fábrica."),
      ),
    );
    const usuario = await abrir();
    const antes = consultas;

    await usuario.click(id("activacion.encender"));
    await usuario.click(id("encendido.confirmar"));

    expect(await within(id("encendido.dialogo")).findByRole("alert")).toHaveTextContent(
      "ONBOARDING_INCOMPLETO",
    );
    await waitFor(() => expect(consultas).toBeGreaterThan(antes));
    expect(id("activacion.resultado").textContent).toBe("");
  });
});

describe("pasos pendientes", () => {
  it("sin repositorio ni ramas: los lista, el botón no abre nada y explica por qué", async () => {
    fabricaApagada();
    faltan({ ramas: true });
    const usuario = await abrir();

    const pendientes = id("activacion.pendientes");
    expect(
      within(pendientes)
        .getAllByTestId("onboarding.activacion.ir")
        .map((b) => b.textContent),
    ).toEqual(["Ir a Repositorio", "Ir a Mapa de ramas"]);
    const boton = id("activacion.encender");
    expect(boton).toHaveAttribute("aria-disabled", "true");
    expect(boton).toHaveAccessibleDescription(
      "Para encenderla falta completar: Ir a Repositorio Ir a Mapa de ramas",
    );

    await usuario.click(boton);
    expect(sinId("encendido.dialogo")).not.toBeInTheDocument();
  });

  it("solo sin repositorio, lista solo ese paso", async () => {
    fabricaApagada();
    faltan({ ramas: false });
    await abrir();

    expect(screen.getAllByTestId("onboarding.activacion.ir").map((b) => b.textContent)).toEqual([
      "Ir a Repositorio",
    ]);
  });

  it("«Ir a» lleva al paso pendiente con el foco en su título", async () => {
    fabricaApagada();
    faltan({ ramas: true });
    const usuario = await abrir();

    await usuario.click(screen.getByRole("button", { name: "Ir a Mapa de ramas" }));

    await waitFor(() => expect(rutaActual()).toBe("/onboarding?paso=3"));
    expect(titulo()).toHaveTextContent("Mapa de ramas");
    expect(titulo()).toHaveFocus();
  });
});

it("el admin ve el estado pero no puede encender la fábrica", async () => {
  fabricaApagada();
  await abrir("admin");

  expect(id("activacion.solo-owner").textContent).toBe(
    "Solo el owner de la organización puede encender la fábrica.",
  );
  expect(sinId("activacion.encender")).not.toBeInTheDocument();
  expect(id("resumen.grafo")).toBeInTheDocument();
});
