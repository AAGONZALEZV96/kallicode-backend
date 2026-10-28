import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../estado/i18n";
import Paginacion from "./Paginacion";

type Props = Parameters<typeof Paginacion>[0];

function montar(props: Partial<Props> = {}) {
  const alCambiar = vi.fn();
  render(
    <I18nProvider>
      <Paginacion
        pagina={1}
        tamano={25}
        total={60}
        enPagina={25}
        alCambiar={alCambiar}
        vista="lista"
        {...props}
      />
    </I18nProvider>,
  );
  return alCambiar;
}

const estado = () => screen.getByTestId("lista.paginacion.estado").textContent;
const anterior = () => screen.getByRole("button", { name: "Página anterior" });
const siguiente = () => screen.getByRole("button", { name: "Página siguiente" });

describe("<Paginacion>", () => {
  it("calcula las páginas con el page_size de la respuesta, no con uno fijo", () => {
    montar({ tamano: 10, total: 25 });

    expect(estado()).toBe("Página 1 de 3");
  });

  it("en la primera página no deja retroceder", async () => {
    const alCambiar = montar();

    expect(anterior()).toBeDisabled();
    await userEvent.click(siguiente());
    expect(alCambiar).toHaveBeenCalledWith(2);
  });

  it("en la última página no deja avanzar", async () => {
    const alCambiar = montar({ pagina: 3, enPagina: 10 });

    expect(estado()).toBe("Página 3 de 3");
    expect(siguiente()).toBeDisabled();
    await userEvent.click(anterior());
    expect(alCambiar).toHaveBeenCalledWith(2);
  });

  it("con una sola página no se muestra", () => {
    montar({ total: 25 });

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("sin total, una página llena indica que puede haber más", () => {
    montar({ total: undefined, pagina: 2, enPagina: 25 });

    expect(estado()).toBe("Página 2");
    expect(siguiente()).toBeEnabled();
  });

  it("sin total, una página incompleta es la última", () => {
    montar({ total: undefined, pagina: 2, enPagina: 7 });

    expect(siguiente()).toBeDisabled();
    expect(anterior()).toBeEnabled();
  });

  it("es una región de navegación con nombre accesible", () => {
    montar();

    expect(screen.getByRole("navigation", { name: "Paginación" })).toBeInTheDocument();
  });
});
