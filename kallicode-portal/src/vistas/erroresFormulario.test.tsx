/**
 * Errores por campo en Nuevo ticket y Nueva funcionalidad (KC-PT-06-5, cierra PT-F18):
 * las reglas del portal antes de enviar y el 422 del backend (su sobre o Problem Details),
 * junto a cada campo, con aria-invalid, aria-describedby y foco en el primero.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ComponentType } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, onTestFinished } from "vitest";

import { fijarSesion } from "../api/client";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import NuevaFuncionalidad from "./NuevaFuncionalidad";
import NuevoTicket from "./NuevoTicket";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

function abrir(Vista: ComponentType) {
  render(
    <Proveedores>
      <MemoryRouter>
        <Vista />
      </MemoryRouter>
    </Proveedores>,
  );
  return userEvent.setup();
}

/** Cuerpos de los POST a `ruta`. */
function enviados(ruta: string) {
  const cuerpos: Record<string, unknown>[] = [];
  const anotar = async ({ request }: { request: Request }) => {
    if (request.method === "POST" && new URL(request.url).pathname === ruta)
      cuerpos.push(await request.clone().json());
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return cuerpos;
}

const responder422 = (ruta: string, campos: { campo: string; error: string }[]) =>
  server.use(
    http.post(`*${ruta}`, () =>
      HttpResponse.json(
        {
          error: {
            codigo: "VALIDACION_ENTRADA",
            mensaje: "Revisa los campos marcados.",
            detalle: { campos },
            trace_id: "tr_422",
          },
        },
        { status: 422 },
      ),
    ),
  );

const nt = (elemento: string) => screen.getByTestId(`nuevo-ticket.${elemento}`);
const nf = (elemento: string) => screen.getByTestId(`nueva-funcionalidad.${elemento}`);

/** El control está marcado y su descripción es el mensaje de error. */
function conError(control: HTMLElement, mensaje: string) {
  expect(control).toHaveAttribute("aria-invalid", "true");
  expect(control).toHaveAccessibleDescription(mensaje);
}

describe("Nuevo ticket", () => {
  it("al enviar vacío marca cada campo, enfoca el primero y no llama al backend", async () => {
    const tickets = enviados("/api/v1/tickets");
    const usuario = abrir(NuevoTicket);

    await usuario.click(nt("formulario.enviar"));

    expect(nt("errores.resumen")).toHaveTextContent("Revisa los campos marcados.");
    expect(nt("errores.resumen")).toHaveAttribute("role", "alert");
    conError(nt("formulario.titulo"), "Este campo es obligatorio.");
    conError(nt("formulario.descripcion"), "Este campo es obligatorio.");
    expect(nt("formulario.modulo")).not.toHaveAttribute("aria-invalid");
    expect(nt("formulario.titulo")).toHaveFocus();
    expect(tickets).toEqual([]);
  });

  it("después del primer intento, los errores se corrigen mientras se escribe", async () => {
    const usuario = abrir(NuevoTicket);
    await usuario.click(nt("formulario.enviar"));

    await usuario.type(nt("formulario.titulo"), "Err");
    conError(nt("formulario.titulo"), "Debe tener al menos 5 caracteres.");

    await usuario.type(nt("formulario.titulo"), "or al exportar");
    expect(nt("formulario.titulo")).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByTestId("nuevo-ticket.errores.titulo")).not.toBeInTheDocument();
  });

  it("antes de intentar enviar no muestra errores", async () => {
    const usuario = abrir(NuevoTicket);

    await usuario.type(nt("formulario.titulo"), "Er");

    expect(nt("formulario.titulo")).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByTestId("nuevo-ticket.errores.resumen")).not.toBeInTheDocument();
  });

  it("un 422 del backend se muestra en su campo, traducido, y enfoca ese campo", async () => {
    responder422("/api/v1/tickets", [
      { campo: "descripcion", error: "String should have at most 10000 characters" },
      { campo: "adjuntos", error: "List should have at most 10 items after validation, not 11" },
    ]);
    const usuario = abrir(NuevoTicket);
    await usuario.type(nt("formulario.titulo"), "Error al exportar");
    await usuario.type(nt("formulario.descripcion"), "El reporte mensual falla");

    await usuario.click(nt("formulario.enviar"));

    await waitFor(() =>
      conError(nt("formulario.descripcion"), "Puede tener como máximo 10000 caracteres."),
    );
    expect(nt("formulario.descripcion")).toHaveFocus();
    // El campo que el formulario no tiene aparece en el resumen.
    expect(nt("errores.resumen")).toHaveTextContent(
      "adjuntos: Puede tener como máximo 10 elementos.",
    );
    expect(nt("formulario.titulo")).not.toHaveAttribute("aria-invalid");
  });

  it("al corregir el campo, el error del backend se va y se puede reenviar", async () => {
    responder422("/api/v1/tickets", [{ campo: "titulo", error: "Valor rechazado por el backend" }]);
    const usuario = abrir(NuevoTicket);
    await usuario.type(nt("formulario.titulo"), "Error al exportar");
    await usuario.type(nt("formulario.descripcion"), "El reporte mensual falla");
    await usuario.click(nt("formulario.enviar"));
    await waitFor(() => conError(nt("formulario.titulo"), "Valor rechazado por el backend"));

    await usuario.type(nt("formulario.titulo"), "!");

    expect(nt("formulario.titulo")).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByTestId("nuevo-ticket.errores.resumen")).not.toBeInTheDocument();
  });

  it("también entiende un 422 en formato Problem Details (RFC 7807)", async () => {
    server.use(
      http.post("*/api/v1/tickets", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Unprocessable Entity",
            status: 422,
            detail: "Revisa los campos marcados.",
            errors: [{ pointer: "/titulo", detail: "String should have at least 5 characters" }],
          },
          { status: 422, headers: { "Content-Type": "application/problem+json" } },
        ),
      ),
    );
    const usuario = abrir(NuevoTicket);
    await usuario.type(nt("formulario.titulo"), "Error al exportar");
    await usuario.type(nt("formulario.descripcion"), "El reporte mensual falla");

    await usuario.click(nt("formulario.enviar"));

    await waitFor(() => conError(nt("formulario.titulo"), "Debe tener al menos 5 caracteres."));
  });

  it("los errores que no son de validación siguen en la caja de error", async () => {
    server.use(
      http.post("*/api/v1/tickets", () =>
        HttpResponse.json(
          {
            error: { codigo: "LIMITE_CREACION", mensaje: "Demasiados tickets.", trace_id: "tr_9" },
          },
          { status: 429 },
        ),
      ),
    );
    const usuario = abrir(NuevoTicket);
    await usuario.type(nt("formulario.titulo"), "Error al exportar");
    await usuario.type(nt("formulario.descripcion"), "El reporte mensual falla");

    await usuario.click(nt("formulario.enviar"));

    expect(await screen.findByTestId("comun.error.caja")).toHaveTextContent("LIMITE_CREACION");
    expect(screen.queryByTestId("nuevo-ticket.errores.resumen")).not.toBeInTheDocument();
  });
});

describe("Nueva funcionalidad", () => {
  const filas = () => screen.getAllByTestId("nueva-funcionalidad.criterios.fila");

  it("un criterio a medias se marca (antes se descartaba sin avisar)", async () => {
    const features = enviados("/api/v1/tickets/features");
    const usuario = abrir(NuevaFuncionalidad);
    await usuario.type(nf("formulario.nombre"), "Exportar a Excel");
    await usuario.type(nf("formulario.objetivo"), "Que finanzas descargue el reporte mensual");
    await usuario.type(
      within(filas()[0]!).getByTestId("nueva-funcionalidad.criterios.dado"),
      "un reporte",
    );

    await usuario.click(nf("formulario.enviar"));

    expect(nf("criterios.error")).toHaveTextContent(
      "Criterio 1 · Cuando: Este campo es obligatorio. · Criterio 1 · Entonces: Este campo es obligatorio.",
    );
    const cuando = within(filas()[0]!).getByTestId("nueva-funcionalidad.criterios.cuando");
    expect(cuando).toHaveAttribute("aria-invalid", "true");
    expect(cuando).toHaveFocus();
    expect(
      within(filas()[0]!).getByTestId("nueva-funcionalidad.criterios.dado"),
    ).not.toHaveAttribute("aria-invalid");
    expect(features).toEqual([]);
  });

  it("un criterio en blanco no se envía ni da error", async () => {
    const features = enviados("/api/v1/tickets/features");
    const usuario = abrir(NuevaFuncionalidad);
    await usuario.type(nf("formulario.nombre"), "Exportar a Excel");
    await usuario.type(nf("formulario.objetivo"), "Que finanzas descargue el reporte mensual");

    await usuario.click(nf("formulario.enviar"));

    await waitFor(() => expect(features).toHaveLength(1));
    expect(features[0]).toMatchObject({ criterios: [] });
    expect(screen.queryByTestId("nueva-funcionalidad.criterios.error")).not.toBeInTheDocument();
  });

  it("el 422 de un criterio se muestra en su fila aunque haya filas en blanco antes", async () => {
    // El backend solo recibe el criterio de la fila 2: su índice 0 es la fila 2.
    responder422("/api/v1/tickets/features", [
      { campo: "criterios.0.when", error: "String should have at most 500 characters" },
      { campo: "objetivo", error: "String should have at least 20 characters" },
    ]);
    const usuario = abrir(NuevaFuncionalidad);
    await usuario.type(nf("formulario.nombre"), "Exportar a Excel");
    await usuario.type(nf("formulario.objetivo"), "Que finanzas descargue el reporte mensual");
    await usuario.click(nf("criterios.agregar"));
    const fila2 = within(filas()[1]!);
    await usuario.type(
      fila2.getByTestId("nueva-funcionalidad.criterios.dado"),
      "un reporte mensual",
    );
    await usuario.type(fila2.getByTestId("nueva-funcionalidad.criterios.cuando"), "lo descargo");
    await usuario.type(
      fila2.getByTestId("nueva-funcionalidad.criterios.entonces"),
      "obtengo un xlsx",
    );

    await usuario.click(nf("formulario.enviar"));

    await waitFor(() =>
      expect(fila2.getByTestId("nueva-funcionalidad.criterios.cuando")).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    const errores = screen.getAllByTestId("nueva-funcionalidad.criterios.error");
    expect(errores).toHaveLength(1);
    expect(errores[0]).toHaveTextContent(
      "Criterio 2 · Cuando: Puede tener como máximo 500 caracteres.",
    );
    conError(nf("formulario.objetivo"), "Debe tener al menos 20 caracteres.");
    // El primero en el orden del formulario recibe el foco.
    expect(nf("formulario.objetivo")).toHaveFocus();
  });
});
