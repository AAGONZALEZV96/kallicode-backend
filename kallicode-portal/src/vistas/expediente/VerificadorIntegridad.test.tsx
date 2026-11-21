/**
 * Verificador de integridad del expediente (KC-PT-08-4), en el portal completo: la verificación
 * del tramo que trae el expediente (KC-BE-08-2), el primer sello alterado marcado en los
 * eventos y la nueva verificación a pedido con GET /audit/integrity?ticket_id.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Expediente, Rol } from "../../api/tipos";
import { expediente, perfilOwner } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const evento = (id: number, resumen: string) => ({
  id,
  creado_en: `2026-11-1${id}T12:00:00Z`,
  actor_tipo: "agente",
  actor_id: "agente_build",
  resumen,
  sello: `${id}`.repeat(64),
});
const tresEventos = [
  evento(1, "Ticket creado"),
  evento(2, "Spec aprobada"),
  evento(3, "PR abierto"),
];

function responderExpediente(integridad: Partial<Expediente["integridad"]>, eventos = tresEventos) {
  server.use(
    http.get("*/api/v1/audit/tickets/:numero", () =>
      HttpResponse.json({
        ...expediente,
        integridad: {
          ...expediente.integridad,
          eventos_verificados: eventos.length,
          ...integridad,
        },
        eventos,
      }),
    ),
  );
}

/** URLs de cada GET /audit/integrity. */
function verificaciones() {
  const urls: URL[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/v1/audit/integrity") urls.push(url);
  });
  return urls;
}

async function abrir(rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde("/auditoria/KC-1001");
  await screen.findByTestId("expediente.integridad.estado");
  return usuario;
}

const t = (id: string) => screen.getByTestId(id);
/** data-sello de cada evento de la lista, en orden. */
const sellos = () =>
  within(t("expediente.eventos.lista"))
    .getAllByRole("listitem")
    .map((li) => li.getAttribute("data-sello"));

describe("la verificación que trae el expediente", () => {
  it("muestra el tramo verificado, su alcance y el sello final", async () => {
    responderExpediente({
      desde: "2026-11-11T12:00:00Z",
      hasta: "2026-11-13T12:00:00Z",
      hash_raiz: "3".repeat(64),
    });
    await abrir();

    expect(t("expediente.integridad.estado")).toHaveTextContent("✓ Cadena de hashes íntegra");
    expect(t("expediente.integridad.tramo")).toHaveTextContent(
      /Tramo del ticket \(del .+ al .+\) · eventos verificados: 3\. Solo se verifican los sellos de este ticket/,
    );
    expect(screen.getByTitle("3".repeat(64))).toHaveTextContent("Sello final: 33333333…3333");
    expect(screen.queryByTestId("expediente.integridad.roto")).not.toBeInTheDocument();
    expect(sellos()).toEqual(["confirmado", "confirmado", "confirmado"]);
  });

  it("con un sello alterado señala el primero; los anteriores quedan confirmados y los posteriores no", async () => {
    responderExpediente({ integra: false, tramo_integro: false, primer_evento_roto: 2 });
    await abrir();

    expect(t("expediente.integridad.estado")).toHaveTextContent("✗ ¡Cadena de hashes ROTA!");
    expect(t("expediente.integridad.roto")).toHaveTextContent(
      "Primer evento alterado: #2 — «Spec aprobada». Los eventos posteriores quedan sin confirmar.",
    );
    expect(sellos()).toEqual(["confirmado", "alterado", "sin-confirmar"]);
    const lista = within(t("expediente.eventos.lista"));
    expect(lista.getByText("✗ alterado")).toBeInTheDocument();
    expect(lista.getByText("? sin confirmar")).toBeInTheDocument();
  });

  it("si el evento alterado no está en la lista, igual lo nombra por su id", async () => {
    responderExpediente({ integra: false, tramo_integro: false, primer_evento_roto: 9 });
    await abrir();

    expect(t("expediente.integridad.roto")).toHaveTextContent(
      "Primer evento alterado: #9. Los eventos posteriores quedan sin confirmar.",
    );
    expect(sellos()).toEqual(["confirmado", "confirmado", "confirmado"]);
  });

  it("sin eventos no dice «íntegra»: no hay nada verificado", async () => {
    responderExpediente({ eventos_verificados: 0, desde: null, hasta: null, hash_raiz: null }, []);
    await abrir();

    expect(t("expediente.integridad.estado")).toHaveTextContent("Sin eventos que verificar");
    expect(t("expediente.integridad.estado")).not.toHaveClass("pass");
    expect(t("expediente.integridad.tramo")).not.toHaveTextContent("Tramo del ticket");
    expect(screen.queryByText(/Sello final/)).not.toBeInTheDocument();
  });
});

describe("verificar de nuevo", () => {
  it("pide el tramo del ticket y anuncia el resultado", async () => {
    const urls = verificaciones();
    const usuario = await abrir();
    expect(t("expediente.integridad.resultado")).toHaveAttribute("role", "status");
    expect(t("expediente.integridad.resultado")).toBeEmptyDOMElement();

    await usuario.click(t("expediente.integridad.verificar"));

    await waitFor(() =>
      expect(t("expediente.integridad.resultado")).toHaveTextContent(
        /^Verificado a las .+ · eventos: 1 · cadena íntegra\.$/,
      ),
    );
    expect(urls.map((u) => u.search)).toEqual(["?ticket_id=KC-1001"]);
    expect(t("expediente.integridad.verificar")).toHaveAttribute("aria-disabled", "false");
  });

  it("si ahora hay un sello alterado, la vista cambia aunque el expediente decía íntegra", async () => {
    responderExpediente({});
    server.use(
      http.get("*/api/v1/audit/integrity", () =>
        HttpResponse.json({
          integra: false,
          alcance: "tramo_ticket",
          tramo_integro: false,
          cadena_tenant_integra: null,
          eventos_verificados: 3,
          primer_evento_roto: 3,
        }),
      ),
    );
    const usuario = await abrir();
    expect(t("expediente.integridad.estado")).toHaveTextContent("✓");

    await usuario.click(t("expediente.integridad.verificar"));

    await waitFor(() =>
      expect(t("expediente.integridad.estado")).toHaveTextContent("✗ ¡Cadena de hashes ROTA!"),
    );
    expect(t("expediente.integridad.resultado")).toHaveTextContent(
      /· eventos: 3 · hay sellos alterados\./,
    );
    expect(t("expediente.integridad.roto")).toHaveTextContent("#3 — «PR abierto»");
    expect(sellos()).toEqual(["confirmado", "confirmado", "alterado"]);
  });

  it("después de verificar vuelve a pedir el expediente: la lista muestra el evento como está ahora", async () => {
    let pedidos = 0;
    server.use(
      http.get("*/api/v1/audit/tickets/:numero", () => {
        pedidos += 1;
        const eventos =
          pedidos === 1
            ? tresEventos
            : [
                tresEventos[0],
                { ...tresEventos[1]!, resumen: "Spec aprobada (editada)" },
                tresEventos[2],
              ];
        const rota = pedidos > 1;
        return HttpResponse.json({
          ...expediente,
          integridad: {
            ...expediente.integridad,
            eventos_verificados: 3,
            integra: !rota,
            tramo_integro: !rota,
            primer_evento_roto: rota ? 2 : null,
          },
          eventos,
        });
      }),
      http.get("*/api/v1/audit/integrity", () =>
        HttpResponse.json({
          integra: false,
          alcance: "tramo_ticket",
          tramo_integro: false,
          eventos_verificados: 3,
          primer_evento_roto: 2,
        }),
      ),
    );
    const usuario = await abrir();
    expect(screen.getByText("Spec aprobada")).toBeInTheDocument();

    await usuario.click(t("expediente.integridad.verificar"));

    expect(await screen.findByText("Spec aprobada (editada)")).toBeInTheDocument();
    expect(t("expediente.integridad.roto")).toHaveTextContent("#2 — «Spec aprobada (editada)»");
    expect(sellos()).toEqual(["confirmado", "alterado", "sin-confirmar"]);
    expect(pedidos).toBe(2);
  });

  it("mientras verifica, otro clic no pide otra verificación", async () => {
    responderExpediente({});
    let soltar: () => void = () => {};
    const espera = new Promise<void>((r) => (soltar = r));
    server.use(
      http.get("*/api/v1/audit/integrity", async () => {
        await espera;
        return HttpResponse.json({
          integra: true,
          alcance: "tramo_ticket",
          tramo_integro: true,
          eventos_verificados: 3,
          primer_evento_roto: null,
        });
      }),
    );
    const urls = verificaciones();
    const usuario = await abrir();

    await usuario.click(t("expediente.integridad.verificar"));
    expect(await screen.findByText("Verificando los sellos…")).toBeInTheDocument();
    expect(t("expediente.integridad.verificar")).toHaveAttribute("aria-disabled", "true");
    await usuario.click(t("expediente.integridad.verificar"));
    soltar();

    await waitFor(() =>
      expect(t("expediente.integridad.resultado")).toHaveTextContent("cadena íntegra"),
    );
    expect(urls).toHaveLength(1);
  });

  it("si la verificación falla lo dice, conserva lo que se sabía y se puede reintentar", async () => {
    responderExpediente({});
    server.use(
      http.get("*/api/v1/audit/integrity", () =>
        sobreError(429, "RATE_LIMITED", "Demasiadas solicitudes."),
      ),
    );
    const usuario = await abrir();

    await usuario.click(t("expediente.integridad.verificar"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(t("expediente.integridad.estado")).toHaveTextContent("✓ Cadena de hashes íntegra");
    expect(t("expediente.integridad.resultado")).toBeEmptyDOMElement();
    expect(t("expediente.integridad.verificar")).toHaveAttribute("aria-disabled", "false");
  });

  it.each<Rol>(["admin", "architect"])("el %s también puede verificar", async (rol) => {
    responderExpediente({});
    await abrir(rol);
    expect(t("expediente.integridad.verificar")).toBeInTheDocument();
  });

  it("el approver ve la verificación del expediente, pero no la repite (el backend respondería 403)", async () => {
    responderExpediente({ integra: false, tramo_integro: false, primer_evento_roto: 2 });
    await abrir("approver");

    expect(t("expediente.integridad.roto")).toBeInTheDocument();
    expect(screen.queryByTestId("expediente.integridad.verificar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("expediente.integridad.resultado")).not.toBeInTheDocument();
  });
});
