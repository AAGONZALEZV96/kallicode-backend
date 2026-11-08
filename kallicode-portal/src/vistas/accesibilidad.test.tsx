/**
 * Base de accesibilidad (KC-PT-05-4, cierra PT-F12): rótulos asociados con htmlFor y useId,
 * patrón tablist en el detalle del ticket y navegación por teclado.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { fijarSesion } from "../api/client";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, Proveedores, renderPortal, rutaActual } from "../test/portal";
import NuevaFuncionalidad from "./NuevaFuncionalidad";
import NuevoTicket from "./NuevoTicket";

/** Controles visibles sin nombre accesible: rótulo, aria-label o texto. */
function controlesSinNombre(): string[] {
  const controles = document.querySelectorAll<HTMLElement>(
    "input, select, textarea, button, a[href], [role='tab'], [role='progressbar']",
  );
  return [...controles]
    .filter((el) => !el.hidden && el.getAttribute("type") !== "hidden")
    .filter((el) => {
      try {
        expect(el).toHaveAccessibleName();
        return false;
      } catch {
        return true;
      }
    })
    .map((el) => el.getAttribute("data-testid") ?? el.outerHTML.slice(0, 80));
}

describe("cada control tiene un nombre accesible", () => {
  it("login", async () => {
    renderPortal("/login");
    await screen.findByTestId("login.formulario.correo");

    expect(controlesSinNombre()).toEqual([]);
  });

  it.each([
    ["/", "tablero.vista.raiz"],
    ["/tickets", "tickets.lista.tabla"],
    ["/tickets/KC-1003", "detalle-ticket.gate.panel"],
    ["/nuevo-ticket", "nuevo-ticket.vista.raiz"],
    ["/nueva-funcionalidad", "nueva-funcionalidad.vista.raiz"],
    ["/aprobaciones", "aprobaciones.vista.raiz"],
    ["/auditoria", "auditoria.lista.tabla"],
    ["/auditoria/KC-1001", "expediente.firmas.tabla"],
    ["/conexiones", "conexiones.vista.raiz"],
    ["/modelos", "modelos-ia.asignaciones.tabla"],
    ["/consumo", "consumo.tickets.tabla"],
  ])("%s", async (ruta, cargada) => {
    await entrarDesde(ruta);
    await screen.findByTestId(cargada);

    expect(controlesSinNombre()).toEqual([]);
  });
});

describe("rótulos asociados con htmlFor", () => {
  beforeEach(() => fijarSesion("acc_1", "ref_1"));
  const montar = (vista: React.ReactNode) =>
    render(
      <Proveedores>
        <MemoryRouter>{vista}</MemoryRouter>
      </Proveedores>,
    );

  it("login", () => {
    renderPortal("/login");

    expect(screen.getByLabelText("Correo")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute("type", "password");
  });

  it("nuevo ticket", () => {
    montar(<NuevoTicket />);

    expect(screen.getByLabelText("Título")).toBe(
      screen.getByTestId("nuevo-ticket.formulario.titulo"),
    );
    expect(screen.getByLabelText(/^Descripción/)).toBe(
      screen.getByTestId("nuevo-ticket.formulario.descripcion"),
    );
    expect(screen.getByLabelText("Prioridad")).toBe(
      screen.getByTestId("nuevo-ticket.formulario.prioridad"),
    );
    expect(screen.getByLabelText(/^Módulo/)).toBe(
      screen.getByTestId("nuevo-ticket.formulario.modulo"),
    );
  });

  it("el tipo de ticket es un grupo de botones que indica cuál está elegido", async () => {
    montar(<NuevoTicket />);
    const tipo = screen.getByRole("group", { name: "Tipo" });
    const bug = within(tipo).getByRole("button", { name: "Bug" });
    const mejora = within(tipo).getByRole("button", { name: "Mejora" });
    expect(bug).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(mejora);

    expect(mejora).toHaveAttribute("aria-pressed", "true");
    expect(bug).toHaveAttribute("aria-pressed", "false");
  });

  it("nueva funcionalidad: cada campo de un criterio lleva el número del criterio", async () => {
    montar(<NuevaFuncionalidad />);

    expect(screen.getByLabelText("Nombre de la funcionalidad")).toBe(
      screen.getByTestId("nueva-funcionalidad.formulario.nombre"),
    );
    expect(screen.getByLabelText("Objetivo de negocio")).toBe(
      screen.getByTestId("nueva-funcionalidad.formulario.objetivo"),
    );
    await userEvent.click(screen.getByTestId("nueva-funcionalidad.criterios.agregar"));

    for (const campo of ["Dado que", "Cuando", "Entonces"]) {
      expect(screen.getByLabelText(`Criterio 2 · ${campo}`)).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Quitar el criterio 2" })).toBeInTheDocument();
  });
});

describe("pestañas del detalle (patrón tablist)", () => {
  async function abrirDetalle() {
    const usuario = await entrarDesde("/tickets/KC-1001");
    const lista = await screen.findByRole("tablist", { name: "Secciones del ticket" });
    const tab = (nombre: string) => within(lista).getByRole("tab", { name: nombre });
    return { usuario, tab };
  }

  it("solo la pestaña activa está en el orden de Tab y el panel la nombra", async () => {
    const { tab } = await abrirDetalle();

    expect(tab("Spec")).toHaveAttribute("aria-selected", "true");
    expect(tab("Spec")).toHaveAttribute("tabindex", "0");
    for (const otra of ["Evidencia QA", "Seguridad", "Consumo", "Actividad"]) {
      expect(tab(otra)).toHaveAttribute("aria-selected", "false");
      expect(tab(otra)).toHaveAttribute("tabindex", "-1");
    }
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Spec");
    expect(tab("Spec")).toHaveAttribute("aria-controls", screen.getByRole("tabpanel").id);
  });

  it("las flechas recorren las pestañas en círculo e Inicio/Fin van a los extremos", async () => {
    const { usuario, tab } = await abrirDetalle();
    tab("Spec").focus();

    const pasos: [string, string][] = [
      ["{ArrowRight}", "Evidencia QA"],
      ["{End}", "Actividad"],
      ["{ArrowRight}", "Spec"],
      ["{ArrowLeft}", "Actividad"],
      ["{Home}", "Spec"],
    ];
    for (const [tecla, esperada] of pasos) {
      await usuario.keyboard(tecla);
      expect(tab(esperada)).toHaveFocus();
      expect(tab(esperada)).toHaveAttribute("aria-selected", "true");
      expect(screen.getByRole("tabpanel")).toHaveAccessibleName(esperada);
    }
  });
});

describe("navegación por teclado", () => {
  it("el primer Tab ofrece saltar al contenido principal", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    (document.activeElement as HTMLElement | null)?.blur();

    await usuario.tab();

    const saltar = screen.getByRole("link", { name: "Saltar al contenido" });
    expect(saltar).toHaveFocus();
    expect(saltar).toHaveAttribute("href", "#contenido");
    expect(screen.getByRole("main")).toHaveAttribute("id", "contenido");
  });

  it.each([
    ["/tickets", "KC-1001", "/tickets/KC-1001"],
    ["/auditoria", "KC-1001", "/auditoria/KC-1001"],
    ["/", "KC-1003", "/tickets/KC-1003"],
  ])("en %s la fila se abre con Enter desde el enlace de su id", async (ruta, id, destino) => {
    const usuario = await entrarDesde(ruta);
    const enlace = await screen.findByRole("link", { name: id });

    enlace.focus();
    await usuario.keyboard("{Enter}");

    await waitFor(() => expect(rutaActual()).toBe(destino));
  });

  it("el idioma del documento sigue al idioma elegido", async () => {
    const usuario = await entrarDesde("/");
    expect(document.documentElement.lang).toBe("es");

    await usuario.selectOptions(screen.getByRole("combobox", { name: "Idioma" }), "en");

    expect(document.documentElement.lang).toBe("en");
  });
});

describe("anuncios para lectores de pantalla", () => {
  it("un error del backend se anuncia como alerta", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        sobreError(401, "CREDENCIALES_INVALIDAS", "Credenciales inválidas."),
      ),
    );
    const { correo, clave } = renderPortal("/login");
    const usuario = userEvent.setup();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("Credenciales inválidas.");
  });

  it("la cuota de LOC es una barra de progreso con su valor", async () => {
    await entrarDesde("/consumo");

    const barra = await screen.findByRole("progressbar", { name: /Cuota mensual de LOC/ });
    expect(barra).toHaveAttribute("aria-valuenow");
    expect(barra).toHaveAttribute("aria-valuemax", "100");
  });
});
