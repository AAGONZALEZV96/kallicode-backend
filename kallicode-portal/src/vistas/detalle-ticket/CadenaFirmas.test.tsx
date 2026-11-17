/**
 * Cadena de firmas (KC-PT-06-4): los tres gates en orden con su estado, quién firmó, el
 * comentario y el sello; el enlace al expediente según el rol; y que se actualiza al firmar.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { CadenaFirmas, Rol } from "../../api/tipos";
import { detalleTicket, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde, rutaActual } from "../../test/portal";

const SELLO = "9f2c4e7a1b3d5f60aa11bb22cc33dd44ee55ff6600112233445566778899aabb";

const conCadena = (cadena: CadenaFirmas) =>
  server.use(http.get("*/api/v1/tickets/:numero/gates", () => HttpResponse.json(cadena)));

/** Gate 1 firmado y Gate 2 esperando firma (KC-1003 pasa a QA). */
const g1Firmado: CadenaFirmas = {
  gates: [
    {
      gate: 1,
      estado: "firmado",
      actor: "Ana Torres",
      fecha: "2026-11-10T14:30:00Z",
      comentario: "Alternativa B por el cliente",
      sello: SELLO,
    },
    { gate: 2, estado: "pendiente" },
    { gate: 3, estado: "no_alcanzado" },
  ],
};

function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

const cadena = () => screen.findByTestId("detalle-ticket.firmas.cadena");
const gates = () => screen.getAllByTestId("detalle-ticket.firmas.gate");

describe("cadena de firmas", () => {
  it("muestra los tres gates en orden: quién firmó, cuándo, su comentario y el sello", async () => {
    conCadena(g1Firmado);
    await entrarDesde("/tickets/KC-1003");
    const tarjeta = within(await cadena());
    await tarjeta.findByText("Ana Torres", { exact: false });

    expect(tarjeta.getByRole("list", { name: "Cadena de firmas" })).toBeInTheDocument();
    expect(gates().map((g) => g.querySelector("b")?.textContent)).toEqual([
      "GATE 1 · Aprobar diseño",
      "GATE 2 · Validar evidencia",
      "GATE 3 · Firma de arquitecto",
    ]);
    expect(screen.getAllByTestId("detalle-ticket.firmas.estado").map((e) => e.textContent)).toEqual(
      ["Firmado", "Esperando firma", "Aún no se alcanza"],
    );

    const [g1, g2, g3] = gates();
    expect(within(g1!).getByText("Alternativa B por el cliente")).toBeInTheDocument();
    expect(within(g1!).getByRole("time")).toHaveAttribute("dateTime", "2026-11-10T14:30:00Z");
    // El sello va completo (se lee y se copia entero); el CSS lo recorta.
    expect(within(g1!).getByTestId("detalle-ticket.firmas.sello")).toHaveTextContent(
      `Sello: ${SELLO}`,
    );
    expect(g2).toHaveAttribute("aria-current", "step");
    expect(g1).not.toHaveAttribute("aria-current");
    expect(within(g3!).queryByTestId("detalle-ticket.firmas.sello")).not.toBeInTheDocument();
  });

  it("con firmas, enlaza al expediente donde se verifica la cadena", async () => {
    conCadena(g1Firmado);
    const usuario = await entrarDesde("/tickets/KC-1003");

    await usuario.click(
      await screen.findByRole("link", { name: "Verificar la cadena en el expediente →" }),
    );

    await waitFor(() => expect(rutaActual()).toBe("/auditoria/KC-1003"));
  });

  it("sin firmas todavía no hay nada que verificar: no enlaza al expediente", async () => {
    await entrarDesde("/tickets/KC-1003");
    await cadena();
    await waitFor(() => expect(gates()).toHaveLength(3));

    expect(gates()[0]).toHaveAttribute("aria-current", "step");
    expect(screen.queryByTestId("detalle-ticket.firmas.expediente")).not.toBeInTheDocument();
  });

  it("un rol sin acceso al expediente ve la cadena pero no el enlace", async () => {
    conRol("member");
    conCadena(g1Firmado);
    await entrarDesde("/tickets/KC-1003");

    expect(await screen.findByTestId("detalle-ticket.firmas.sello")).toBeInTheDocument();
    expect(screen.queryByTestId("detalle-ticket.firmas.expediente")).not.toBeInTheDocument();
  });

  it("si la cadena no carga, lo dice en su tarjeta y el resto del detalle sigue", async () => {
    server.use(
      http.get("*/api/v1/tickets/:numero/gates", () =>
        sobreError(503, "DEPENDENCIA_NO_DISPONIBLE", "Base no disponible."),
      ),
    );
    await entrarDesde("/tickets/KC-1003");

    expect(await within(await cadena()).findByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId("detalle-ticket.pestanas.spec")).toBeInTheDocument();
  });
});

describe("al firmar desde el portal", () => {
  it("la cadena se actualiza enseguida con la firma y su sello", async () => {
    let firmado = false;
    server.use(
      http.post("*/api/v1/tickets/:numero/gates/1/approve", () => {
        firmado = true;
        return HttpResponse.json({ gate: 1, estado: "firmado", sello: SELLO });
      }),
      http.get("*/api/v1/tickets/:numero/gates", () =>
        HttpResponse.json(firmado ? g1Firmado : { gates: [] }),
      ),
      http.get("*/api/v1/tickets/:numero", () =>
        HttpResponse.json({ ...detalleTicket("KC-1003"), gate_pendiente: firmado ? 2 : 1 }),
      ),
    );
    const usuario = await entrarDesde("/tickets/KC-1003");
    const panel = await screen.findByRole("region", { name: "GATE 1 · Aprobar diseño" });
    await within(panel).findByRole("combobox", { name: "Alternativa" });

    await usuario.click(within(panel).getByRole("button", { name: "✓ Aprobar" }));
    await usuario.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Firmar" }));

    // Sin esperar al refresco periódico (20 s): la firma invalida la consulta.
    expect(
      await within(await cadena()).findByTestId("detalle-ticket.firmas.sello", undefined, {
        timeout: 2000,
      }),
    ).toHaveTextContent(SELLO);
  });
});
