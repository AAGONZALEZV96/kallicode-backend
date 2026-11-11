/**
 * Modal de cancelación (KC-PT-06-5, cierra PT-F18): reemplaza al prompt() del navegador.
 * KC-1001 está en triage y el owner puede cancelarlo.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished } from "vitest";

import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde, rutaActual } from "../../test/portal";

/** Cuerpos de los PATCH al ticket. */
function patches() {
  const cuerpos: unknown[] = [];
  const anotar = async ({ request }: { request: Request }) => {
    if (request.method === "PATCH") cuerpos.push(await request.clone().json());
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return cuerpos;
}

async function abrirModal() {
  const usuario = await entrarDesde("/tickets/KC-1001");
  const boton = await screen.findByTestId("detalle-ticket.acciones.cancelar");
  await usuario.click(boton);
  const dialogo = screen.getByRole("dialog", { name: "Cancelar KC-1001" });
  const motivo = within(dialogo).getByLabelText("Motivo de la cancelación");
  return { usuario, boton, dialogo, motivo };
}

const confirmar = (dialogo: HTMLElement) =>
  within(dialogo).getByRole("button", { name: "Cancelar ticket" });

describe("modal de cancelación", () => {
  it("abre un diálogo con el aviso y el foco en el motivo; Escape lo cierra sin cancelar", async () => {
    const enviados = patches();
    const { usuario, boton, dialogo, motivo } = await abrirModal();

    expect(boton).toHaveAttribute("aria-haspopup", "dialog");
    expect(dialogo).toHaveTextContent("No se puede deshacer");
    expect(motivo).toHaveFocus();
    expect(motivo).toHaveAccessibleDescription(
      "Entre 5 y 500 caracteres. Queda registrado en la auditoría del ticket.",
    );

    await usuario.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(boton).toHaveFocus();
    expect(enviados).toEqual([]);
  });

  it("un motivo corto se marca en el campo y no se envía nada", async () => {
    const enviados = patches();
    const { usuario, dialogo, motivo } = await abrirModal();

    await usuario.type(motivo, "no");
    await usuario.click(confirmar(dialogo));

    expect(motivo).toHaveAttribute("aria-invalid", "true");
    expect(motivo).toHaveAccessibleDescription(
      "Entre 5 y 500 caracteres. Queda registrado en la auditoría del ticket. Debe tener al menos 5 caracteres.",
    );
    expect(motivo).toHaveFocus();
    expect(enviados).toEqual([]);
  });

  it("con un motivo válido cancela el ticket y vuelve a la lista", async () => {
    const enviados = patches();
    const { usuario, dialogo, motivo } = await abrirModal();

    await usuario.type(motivo, "  Duplicado de KC-1002  ");
    await usuario.click(confirmar(dialogo));

    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
    expect(enviados).toEqual([{ accion: "cancelar", motivo_cancelacion: "Duplicado de KC-1002" }]);
  });

  it("si el backend no permite cancelar, lo explica en el diálogo", async () => {
    server.use(
      http.patch("*/api/v1/tickets/:numero", () =>
        sobreError(409, "CANCELACION_TARDIA", "No se puede cancelar durante el despliegue."),
      ),
    );
    const { usuario, dialogo, motivo } = await abrirModal();

    await usuario.type(motivo, "Ya no se necesita");
    await usuario.click(confirmar(dialogo));

    expect(await within(dialogo).findByRole("alert")).toHaveTextContent(
      "CANCELACION_TARDIA · No se puede cancelar durante el despliegue.",
    );
    expect(motivo).not.toHaveAttribute("aria-invalid");
    expect(rutaActual()).toBe("/tickets/KC-1001");
  });

  it("un 422 del backend sin detalle por campo se muestra en el motivo", async () => {
    server.use(
      http.patch("*/api/v1/tickets/:numero", () =>
        HttpResponse.json(
          {
            error: {
              codigo: "VALIDACION_ENTRADA",
              mensaje: "Indica el motivo de la cancelación.",
              trace_id: "tr_1",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const { usuario, dialogo, motivo } = await abrirModal();

    await usuario.type(motivo, "Ya no se necesita");
    await usuario.click(confirmar(dialogo));

    await waitFor(() => expect(motivo).toHaveAttribute("aria-invalid", "true"));
    expect(within(dialogo).getByTestId("detalle-ticket.cancelacion.error")).toHaveTextContent(
      "Indica el motivo de la cancelación.",
    );
    expect(within(dialogo).queryByTestId("comun.error.caja")).not.toBeInTheDocument();
    expect(motivo).toHaveFocus();
  });
});
