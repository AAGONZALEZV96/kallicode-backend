/**
 * Atajos de teclado, borradores y confirmaciones en el portal completo (KC-PT-11-6).
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { organizacion, perfilOwner } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

const id = (testid: string) => screen.getByTestId(testid);
const borradorGuardado = (formulario: string) =>
  localStorage.getItem(`kc_borrador:org_demo:${perfilOwner.id}:${formulario}`);

function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

describe("atajos de teclado", () => {
  it("«g» y luego una letra navega; «n» abre Nuevo ticket", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");

    await usuario.keyboard("gt");
    expect(await screen.findByTestId("tickets.vista.raiz")).toBeInTheDocument();
    await usuario.keyboard("gc");
    expect(await screen.findByTestId("consumo.vista.raiz")).toBeInTheDocument();
    await usuario.keyboard("n");
    expect(await screen.findByTestId("nuevo-ticket.vista.raiz")).toBeInTheDocument();
  });

  it("«/» lleva al buscador de la vista", async () => {
    const usuario = await entrarDesde("/tickets");
    await screen.findByTestId("tickets.vista.raiz");
    await usuario.keyboard("/");
    expect(id("tickets.filtros.busqueda")).toHaveFocus();
    // El «/» no se escribe en el buscador.
    expect(id("tickets.filtros.busqueda")).toHaveValue("");
  });

  it("mientras se escribe en un campo, las teclas son texto", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.click(await screen.findByTestId("nuevo-ticket.formulario.titulo"));
    await usuario.keyboard("gt n?");
    expect(id("nuevo-ticket.formulario.titulo")).toHaveValue("gt n?");
    expect(rutaActual()).toBe("/nuevo-ticket");
    expect(screen.queryByTestId("layout.atajos.dialogo")).not.toBeInTheDocument();
  });

  it("solo navega a las vistas que el rol puede abrir", async () => {
    conRol("viewer");
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    await usuario.keyboard("ge");
    expect(rutaActual()).toBe("/");
  });

  it("«?» y el botón de la cabecera abren la ayuda, con los atajos del rol", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    await usuario.keyboard("?");
    const dialogo = await screen.findByTestId("layout.atajos.dialogo");
    expect(dialogo).toHaveAttribute("role", "dialog");
    expect(within(dialogo).getByText("Ir a Equipo")).toBeInTheDocument();
    // Con la ayuda abierta, las teclas no navegan.
    await usuario.keyboard("gt");
    expect(rutaActual()).toBe("/");
    await usuario.click(id("layout.atajos.cerrar"));
    expect(screen.queryByTestId("layout.atajos.dialogo")).not.toBeInTheDocument();

    await usuario.click(id("layout.cabecera.atajos"));
    expect(await screen.findByTestId("layout.atajos.dialogo")).toBeInTheDocument();
  });

  it("se pueden desactivar los de una tecla (WCAG 2.1.4) y se recuerda", async () => {
    const usuario = await entrarDesde("/");
    await screen.findByTestId("tablero.vista.raiz");
    await usuario.click(id("layout.cabecera.atajos"));
    await usuario.click(await screen.findByTestId("layout.atajos.activar"));
    expect(id("layout.atajos.activar")).not.toBeChecked();
    expect(localStorage.getItem("kc_atajos")).toBe("desactivados");
    await usuario.click(id("layout.atajos.cerrar"));

    await usuario.keyboard("gt?");
    expect(rutaActual()).toBe("/");
    expect(screen.queryByTestId("layout.atajos.dialogo")).not.toBeInTheDocument();
  });
});

describe("borradores", () => {
  it("Nuevo ticket guarda lo escrito y lo recupera al volver, con un aviso", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.type(await screen.findByTestId("nuevo-ticket.formulario.titulo"), "Exportar CSV");
    await usuario.type(id("nuevo-ticket.formulario.descripcion"), "Falta el botón");
    await usuario.click(id("nuevo-ticket.formulario.tipo-mejora"));
    expect(JSON.parse(borradorGuardado("nuevo-ticket")!).datos).toMatchObject({
      tipo: "mejora",
      titulo: "Exportar CSV",
      descripcion: "Falta el botón",
    });

    await usuario.keyboard("{Escape}");
    await usuario.click(id("layout.nav.tickets"));
    await screen.findByTestId("tickets.vista.raiz");
    await usuario.click(id("layout.nav.nuevo-ticket"));

    expect(await screen.findByTestId("nuevo-ticket.borrador.aviso")).toHaveTextContent(
      "Los archivos adjuntos no se guardan",
    );
    expect(id("nuevo-ticket.formulario.titulo")).toHaveValue("Exportar CSV");
    expect(id("nuevo-ticket.formulario.descripcion")).toHaveValue("Falta el botón");
    expect(id("nuevo-ticket.formulario.tipo-mejora")).toHaveAttribute("aria-pressed", "true");
  });

  it("descartar el borrador pide confirmar y deja el formulario vacío", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.type(await screen.findByTestId("nuevo-ticket.formulario.titulo"), "Borrador");
    await usuario.click(id("layout.nav.tickets"));
    await usuario.click(await screen.findByTestId("layout.nav.nuevo-ticket"));

    await usuario.click(await screen.findByTestId("nuevo-ticket.borrador.descartar"));
    const dialogo = await screen.findByTestId("nuevo-ticket.descartar.dialogo");
    // «Seguir editando» no borra nada.
    await usuario.click(within(dialogo).getByTestId("nuevo-ticket.descartar.cancelar"));
    expect(id("nuevo-ticket.formulario.titulo")).toHaveValue("Borrador");

    await usuario.click(id("nuevo-ticket.borrador.descartar"));
    await usuario.click(await screen.findByTestId("nuevo-ticket.descartar.confirmar"));
    expect(id("nuevo-ticket.formulario.titulo")).toHaveValue("");
    expect(screen.queryByTestId("nuevo-ticket.borrador.aviso")).not.toBeInTheDocument();
    expect(borradorGuardado("nuevo-ticket")).toBeNull();
    expect(rutaActual()).toBe("/nuevo-ticket");
  });

  it("Cancelar con algo escrito pide confirmar; sin nada escrito sale directo", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.click(await screen.findByTestId("nuevo-ticket.formulario.cancelar"));
    expect(await screen.findByTestId("tickets.vista.raiz")).toBeInTheDocument();

    await usuario.click(id("layout.nav.nuevo-ticket"));
    await usuario.type(await screen.findByTestId("nuevo-ticket.formulario.titulo"), "Algo");
    await usuario.click(id("nuevo-ticket.formulario.cancelar"));
    await usuario.click(await screen.findByTestId("nuevo-ticket.descartar.confirmar"));
    expect(await screen.findByTestId("tickets.vista.raiz")).toBeInTheDocument();
    expect(borradorGuardado("nuevo-ticket")).toBeNull();
  });

  it("al crear el ticket el borrador se borra", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.type(
      await screen.findByTestId("nuevo-ticket.formulario.titulo"),
      "Timeout al exportar",
    );
    await usuario.type(id("nuevo-ticket.formulario.descripcion"), "Falla con más de mil filas");
    expect(borradorGuardado("nuevo-ticket")).not.toBeNull();
    await usuario.click(id("nuevo-ticket.formulario.enviar"));
    expect(await screen.findByTestId("detalle-ticket.vista.raiz")).toBeInTheDocument();
    expect(borradorGuardado("nuevo-ticket")).toBeNull();
  });

  it("Nueva funcionalidad recupera nombre, objetivo y criterios", async () => {
    const usuario = await entrarDesde("/nueva-funcionalidad");
    await usuario.type(
      await screen.findByTestId("nueva-funcionalidad.formulario.nombre"),
      "Convenios",
    );
    await usuario.type(id("nueva-funcionalidad.criterios.dado"), "un cliente con convenio");
    await usuario.click(id("layout.nav.tickets"));
    await usuario.click(await screen.findByTestId("layout.nav.nueva-funcionalidad"));

    expect(await screen.findByTestId("nueva-funcionalidad.borrador.aviso")).toBeInTheDocument();
    expect(id("nueva-funcionalidad.formulario.nombre")).toHaveValue("Convenios");
    expect(id("nueva-funcionalidad.criterios.dado")).toHaveValue("un cliente con convenio");
  });

  it("cerrar sesión borra los borradores (computadores compartidos)", async () => {
    const usuario = await entrarDesde("/nuevo-ticket");
    await usuario.type(await screen.findByTestId("nuevo-ticket.formulario.titulo"), "Privado");
    expect(borradorGuardado("nuevo-ticket")).not.toBeNull();
    await usuario.click(id("layout.cabecera.salir"));
    await waitFor(() => expect(borradorGuardado("nuevo-ticket")).toBeNull());
  });
});

describe("encender la fábrica desde Conexiones", () => {
  it("pide confirmar; cancelar no enciende y confirmar sí", async () => {
    let encendidos = 0;
    server.use(
      http.get("*/api/v1/organization", () =>
        HttpResponse.json({ ...organizacion, fabrica_activa: false }),
      ),
      http.post("*/api/v1/onboarding/complete", () => {
        encendidos++;
        return HttpResponse.json({ activado: true, lineas_activadas: 3, pendientes: [] });
      }),
    );
    const usuario = await entrarDesde("/conexiones");
    await usuario.click(await screen.findByTestId("conexiones.cabecera.encender"));
    await usuario.click(await screen.findByTestId("conexiones.encendido.cancelar"));
    expect(encendidos).toBe(0);

    await usuario.click(id("conexiones.cabecera.encender"));
    await usuario.click(await screen.findByTestId("conexiones.encendido.confirmar"));
    expect(await screen.findByTestId("conexiones.avisos.mensaje")).toHaveTextContent(
      "Fábrica encendida con 3 líneas de producción.",
    );
    expect(encendidos).toBe(1);
    expect(screen.queryByTestId("conexiones.encendido.dialogo")).not.toBeInTheDocument();
  });
});
