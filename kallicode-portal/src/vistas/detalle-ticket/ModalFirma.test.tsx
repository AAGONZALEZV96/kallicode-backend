/**
 * Modal de firma de gates (KC-PT-06-7): resumen antes de firmar, sello después, pedir
 * cambios, errores del backend y foco. KC-1003 espera el Gate 1 con dos alternativas.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, onTestFinished } from "vitest";

import { detalleTicket } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

/** Decisiones que recibe el backend: acción y cuerpo. */
function decisiones() {
  const enviadas: { accion: string; cuerpo: unknown }[] = [];
  const anotar = async ({ request }: { request: Request }) => {
    const m = /\/gates\/\d+\/(approve|request-changes)$/.exec(new URL(request.url).pathname);
    if (request.method === "POST" && m)
      enviadas.push({ accion: m[1]!, cuerpo: await request.clone().json() });
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return enviadas;
}

/** Tras decidir, el backend ya no tiene el gate pendiente. */
const sinGatePendiente = () =>
  server.use(
    http.get("*/api/v1/tickets/:numero", () =>
      HttpResponse.json({ ...detalleTicket("KC-1003"), gate_pendiente: null }),
    ),
  );

async function abrirPanel() {
  const usuario = await entrarDesde("/tickets/KC-1003");
  const panel = await screen.findByRole("region", { name: "GATE 1 · Aprobar diseño" });
  await within(panel).findByRole("combobox", { name: "Alternativa" });
  return { usuario, panel };
}

const dialogo = () => screen.getByRole("dialog");

describe("aprobar", () => {
  it("antes de firmar muestra qué se firma y no envía nada", async () => {
    const enviadas = decisiones();
    const { usuario, panel } = await abrirPanel();
    await usuario.selectOptions(within(panel).getByRole("combobox", { name: "Alternativa" }), "B");
    await usuario.type(within(panel).getByLabelText(/^Comentarios/), "Mejor por cliente");

    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));

    expect(screen.getByRole("dialog", { name: "Firmar el GATE 1" })).toBeInTheDocument();
    const resumen = within(dialogo()).getByTestId("detalle-ticket.firma.resumen");
    expect(resumen).toHaveTextContent("KC-1003");
    expect(resumen).toHaveTextContent("GATE 1 · Aprobar diseño");
    expect(resumen).toHaveTextContent("B · Descuento por cliente");
    expect(resumen).toHaveTextContent("Mejor por cliente");
    expect(within(dialogo()).getByText(/no se puede deshacer/)).toBeInTheDocument();
    // El primer control es Cancelar: Enter sin pensar no firma.
    expect(within(dialogo()).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    expect(enviadas).toEqual([]);
  });

  it("al confirmar firma, muestra el sello y al cerrar el foco vuelve al contenido", async () => {
    const enviadas = decisiones();
    const { usuario, panel } = await abrirPanel();
    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));
    sinGatePendiente();

    await usuario.click(within(dialogo()).getByRole("button", { name: "Firmar" }));

    expect(await within(dialogo()).findByText("GATE 1 firmado.")).toBeInTheDocument();
    expect(within(dialogo()).getByTestId("detalle-ticket.firma.sello")).toHaveTextContent(
      "9f2c4e7a1b3d5f60",
    );
    expect(enviadas).toEqual([{ accion: "approve", cuerpo: { alternativa_id: "A" } }]);
    // El panel desapareció (ya no hay gate pendiente), pero el modal sigue con el resultado.
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "GATE 1 · Aprobar diseño" }),
      ).not.toBeInTheDocument(),
    );
    const cerrar = within(dialogo()).getByRole("button", { name: "Cerrar" });
    expect(cerrar).toHaveFocus();

    await usuario.click(cerrar);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("muestra el aviso de cuota que envía el backend al firmar el Gate 1", async () => {
    server.use(
      http.post("*/api/v1/tickets/:numero/gates/:gate/approve", () =>
        HttpResponse.json({
          gate: 1,
          estado: "firmado",
          sello: "abc123",
          ticket: { id: "KC-1003", etapa: "build", gate_pendiente: null },
          aviso_cuota: "La estimación de LOC de esta spec supera la cuota restante del ciclo.",
        }),
      ),
    );
    const { usuario, panel } = await abrirPanel();
    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));

    await usuario.click(within(dialogo()).getByRole("button", { name: "Firmar" }));

    expect(
      await within(dialogo()).findByTestId("detalle-ticket.firma.aviso-cuota"),
    ).toHaveTextContent(/supera la cuota restante/);
  });

  it("Cancelar y Escape cierran sin firmar y devuelven el foco al botón Aprobar", async () => {
    const enviadas = decisiones();
    const { usuario, panel } = await abrirPanel();
    const aprobar = within(panel).getByRole("button", { name: "✓ Aprobar" });

    await usuario.click(aprobar);
    await usuario.click(within(dialogo()).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(aprobar).toHaveFocus();

    await usuario.click(aprobar);
    await usuario.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(aprobar).toHaveFocus();
    expect(enviadas).toEqual([]);
  });

  it("un error del backend se muestra en el modal, que sigue abierto", async () => {
    server.use(
      http.post("*/api/v1/tickets/:numero/gates/:gate/approve", () =>
        sobreError(409, "GATE_NO_ACTIVO", "Este ticket no está esperando el Gate 1."),
      ),
    );
    const { usuario, panel } = await abrirPanel();
    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));

    await usuario.click(within(dialogo()).getByRole("button", { name: "Firmar" }));

    expect(await within(dialogo()).findByRole("alert")).toHaveTextContent("GATE_NO_ACTIVO");
    expect(within(dialogo()).getByRole("button", { name: "Firmar" })).toBeEnabled();
  });

  it("mientras se envía no se puede cerrar ni volver a firmar", async () => {
    server.use(
      http.post("*/api/v1/tickets/:numero/gates/:gate/approve", () => new Promise<never>(() => {})),
    );
    const { usuario, panel } = await abrirPanel();
    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));

    await usuario.click(within(dialogo()).getByRole("button", { name: "Firmar" }));
    await usuario.keyboard("{Escape}");

    expect(dialogo()).toBeInTheDocument();
    expect(within(dialogo()).getByTestId("detalle-ticket.firma.confirmar")).toBeDisabled();
    expect(within(dialogo()).getByRole("button", { name: "Cancelar" })).toBeDisabled();
  });
});

describe("pedir cambios", () => {
  it("con el comentario, confirma y muestra a qué etapa vuelve el ticket", async () => {
    const enviadas = decisiones();
    const { usuario, panel } = await abrirPanel();
    const pedir = within(panel).getByRole("button", { name: "↩ Pedir cambios" });
    expect(pedir).toBeDisabled();
    await usuario.type(
      within(panel).getByLabelText(/^Comentarios/),
      "Falta el caso de clientes sin convenio",
    );
    sinGatePendiente();

    await usuario.click(pedir);
    expect(screen.getByRole("dialog", { name: "Pedir cambios en el GATE 1" })).toBeInTheDocument();
    await usuario.click(within(dialogo()).getByRole("button", { name: "Pedir cambios" }));

    expect(
      await within(dialogo()).findByText(
        "Cambios pedidos: el ticket vuelve a Diseño / Spec (iteración 2).",
      ),
    ).toBeInTheDocument();
    expect(within(dialogo()).queryByTestId("detalle-ticket.firma.sello")).not.toBeInTheDocument();
    expect(enviadas).toEqual([
      {
        accion: "request-changes",
        cuerpo: { comentario: "Falta el caso de clientes sin convenio" },
      },
    ]);
  });
});
