import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Proveedores } from "../test/portal";
import { EsqueletoLista, EsqueletoTabla, EsqueletoTarjetas } from "./Esqueleto";
import { EstadoVacio, FilaVacia } from "./EstadoVacio";

describe("EstadoVacio", () => {
  it("título, descripción y acción", () => {
    render(
      <EstadoVacio
        testid="prueba.lista.vacio"
        titulo="Nada por aquí"
        descripcion="Por qué está vacío"
        accion={<button type="button">Limpiar filtros</button>}
      />,
    );
    const vacio = screen.getByTestId("prueba.lista.vacio");
    expect(within(vacio).getByText("Nada por aquí")).toHaveClass("estado-vacio-titulo");
    expect(within(vacio).getByText("Por qué está vacío")).toBeInTheDocument();
    expect(within(vacio).getByRole("button", { name: "Limpiar filtros" })).toBeInTheDocument();
    expect(vacio.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("sin descripción ni acción, solo el título", () => {
    render(<EstadoVacio testid="prueba.lista.vacio" titulo="Vacío" compacto />);
    const vacio = screen.getByTestId("prueba.lista.vacio");
    expect(vacio).toHaveClass("estado-vacio", "compacto");
    expect(vacio.querySelectorAll("p")).toHaveLength(1);
  });

  it("en una tabla, una fila que ocupa todas las columnas con el testid en la fila", () => {
    render(
      <table>
        <tbody>
          <FilaVacia columnas={5} testid="prueba.tabla.vacio" titulo="Sin filas" />
        </tbody>
      </table>,
    );
    const fila = screen.getByTestId("prueba.tabla.vacio");
    expect(fila.tagName).toBe("TR");
    expect(within(fila).getByRole("cell")).toHaveAttribute("colspan", "5");
    expect(fila).toHaveTextContent("Sin filas");
  });
});

describe("esqueletos", () => {
  it.each([
    ["tabla", <EsqueletoTabla key="t" columnas={4} filas={3} />, ".esqueleto-fila", 4],
    ["lista", <EsqueletoLista key="l" elementos={5} />, ".esqueleto-elemento", 5],
    ["tarjetas", <EsqueletoTarjetas key="c" tarjetas={2} />, ".esqueleto-tarjeta", 2],
  ])("%s: anuncia «Cargando…» y dibuja la silueta decorativa", (_, el, clase, n) => {
    render(<Proveedores>{el}</Proveedores>);
    const indicador = screen.getByRole("status");
    expect(indicador).toHaveAttribute("data-testid", "comun.carga.indicador");
    expect(indicador).toHaveAttribute("aria-busy", "true");
    expect(indicador).toHaveTextContent(/^Cargando…$/);
    const silueta = indicador.querySelector("[aria-hidden='true']")!;
    expect(silueta.querySelectorAll(clase)).toHaveLength(n);
  });

  it("con anunciar=false es solo decorativa: sin role=status ni testid", () => {
    const { container } = render(
      <Proveedores>
        <EsqueletoTabla columnas={3} anunciar={false} />
      </Proveedores>,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("comun.carga.indicador")).not.toBeInTheDocument();
    expect(container.querySelector(".esqueleto")).toHaveAttribute("aria-hidden", "true");
  });
});
