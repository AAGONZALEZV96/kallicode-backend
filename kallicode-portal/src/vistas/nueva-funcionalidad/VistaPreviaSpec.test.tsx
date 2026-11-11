/**
 * Vista previa de la spec en Nueva funcionalidad (KC-PT-06-6): se actualiza al escribir y
 * muestra los criterios con el formato de tickets.crear_feature.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, onTestFinished } from "vitest";

import { fijarSesion } from "../../api/client";
import { server } from "../../mocks/server";
import { Proveedores } from "../../test/portal";
import NuevaFuncionalidad from "../NuevaFuncionalidad";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

function abrir() {
  render(
    <Proveedores>
      <MemoryRouter>
        <NuevaFuncionalidad />
      </MemoryRouter>
    </Proveedores>,
  );
  return userEvent.setup();
}

const id = (elemento: string) => screen.getByTestId(`nueva-funcionalidad.${elemento}`);
const panel = () => within(id("vista-previa.panel"));
const criteriosPrevios = () =>
  screen.queryAllByTestId("nueva-funcionalidad.vista-previa.criterio").map((c) => c.textContent);
const fila = (n: number) => within(screen.getAllByTestId("nueva-funcionalidad.criterios.fila")[n]!);

describe("vista previa de la spec", () => {
  it("vacía al empezar", () => {
    abrir();

    expect(
      screen.getByRole("region", { name: "Así la recibe el arquitecto IA" }),
    ).toBeInTheDocument();
    expect(id("vista-previa.vacio")).toHaveTextContent(
      "Escribe el nombre, el objetivo y los criterios para ver la vista previa.",
    );
  });

  it("muestra nombre y objetivo mientras se escriben, y avisa si no hay criterios", async () => {
    const usuario = abrir();

    await usuario.type(id("formulario.nombre"), "Exportar a Excel");

    expect(id("vista-previa.nombre")).toHaveTextContent("Exportar a Excel");
    expect(id("vista-previa.objetivo")).toHaveTextContent("(falta)");
    expect(
      panel().getByText("Sin criterios: el arquitecto IA los propondrá en la spec."),
    ).toBeInTheDocument();

    await usuario.type(id("formulario.objetivo"), "Que finanzas descargue el reporte");

    expect(id("vista-previa.objetivo")).toHaveTextContent("Que finanzas descargue el reporte");
  });

  it("cada criterio sale como «Dado …, cuando …, entonces …», como lo arma el backend", async () => {
    const usuario = abrir();
    await usuario.type(fila(0).getByTestId("nueva-funcionalidad.criterios.dado"), "un reporte");
    await usuario.type(fila(0).getByTestId("nueva-funcionalidad.criterios.cuando"), "lo descargo");
    await usuario.type(
      fila(0).getByTestId("nueva-funcionalidad.criterios.entonces"),
      "obtengo un xlsx",
    );

    expect(criteriosPrevios()).toEqual([
      "Dado un reporte, cuando lo descargo, entonces obtengo un xlsx",
    ]);
    expect(panel().getByText("Criterios de aceptación propuestos")).toBeInTheDocument();
  });

  it("omite los criterios en blanco y marca lo que falta en uno a medias", async () => {
    const usuario = abrir();
    await usuario.click(id("criterios.agregar"));
    await usuario.type(fila(1).getByTestId("nueva-funcionalidad.criterios.dado"), "un reporte");

    expect(criteriosPrevios()).toEqual(["Dado un reporte, cuando (falta), entonces (falta)"]);
  });

  it("muestra lo mismo que termina en la descripción del ticket", async () => {
    const cuerpos: { criterios: { given: string; when: string; then: string }[] }[] = [];
    const anotar = async ({ request }: { request: Request }) => {
      if (new URL(request.url).pathname === "/api/v1/tickets/features")
        cuerpos.push(await request.clone().json());
    };
    server.events.on("request:start", anotar);
    onTestFinished(() => {
      server.events.removeListener("request:start", anotar);
    });
    const usuario = abrir();
    await usuario.type(id("formulario.nombre"), "Exportar a Excel");
    await usuario.type(id("formulario.objetivo"), "Que finanzas descargue el reporte mensual");
    await usuario.click(id("criterios.agregar"));
    await usuario.type(fila(1).getByTestId("nueva-funcionalidad.criterios.dado"), "un reporte");
    await usuario.type(fila(1).getByTestId("nueva-funcionalidad.criterios.cuando"), "lo descargo");
    await usuario.type(
      fila(1).getByTestId("nueva-funcionalidad.criterios.entonces"),
      "obtengo un xlsx",
    );
    const previa = criteriosPrevios();

    await usuario.click(id("formulario.enviar"));

    await waitFor(() => expect(cuerpos).toHaveLength(1));
    // tickets.crear_feature: "- Dado {given}, cuando {when}, entonces {then}" por criterio.
    const comoBackend = cuerpos[0]!.criterios.map(
      (c) => `Dado ${c.given}, cuando ${c.when}, entonces ${c.then}`,
    );
    expect(previa).toEqual(comoBackend);
  });
});
