/**
 * Recorrido del ciclo comercial (KC-PT-09-6), en el portal completo: con la cuota de LOC agotada
 * y el almacenamiento al 80 %, los avisos llevan a Consumo, se comparan los planes, se solicita
 * la mejora y se ve abierta; tras la aprobación del equipo comercial, el portal muestra el plan
 * nuevo y los tickets que siguen en cola.
 *
 * El backend responde con lo grabado del backend real en
 * apps/api/tests/integracion/ciclo_comercial_desde_portal.json, que
 * test_ciclo_comercial_desde_portal.py vuelve a pedir en cada corrida y compara con lo grabado.
 * Aquí se comprueba que el portal muestra esas respuestas y manda exactamente lo del archivo.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";

import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

interface Solicitud {
  paso: string;
  metodo: string;
  ruta: string;
  interna?: boolean;
  cuerpo?: Record<string, unknown>;
}
interface Contrato {
  solicitudes: Solicitud[];
  respuestas: Record<string, { status: number; cuerpo: unknown }>;
}

const CONTRATO: Contrato = JSON.parse(
  readFileSync(resolve("../apps/api/tests/integracion/ciclo_comercial_desde_portal.json"), "utf-8"),
);
const { respuestas, solicitudes } = CONTRATO;
const responder = (paso: string) =>
  HttpResponse.json(respuestas[paso]!.cuerpo as object, { status: respuestas[paso]!.status });
const texto = (el: HTMLElement) => (el.textContent ?? "").replace(/\s/g, " ");

/** El backend grabado, según el momento del recorrido; devuelve lo que el portal pidió. */
function backendGrabado() {
  const fase = { solicitada: false, aprobada: false };
  const recibidas: { metodo: string; ruta: string; cuerpo?: unknown }[] = [];
  server.events.on("request:start", async ({ request }) => {
    const ruta = new URL(request.url).pathname;
    if (!ruta.startsWith("/api/v1/plans") && ruta !== "/api/v1/usage/summary") return;
    const cuerpo = request.method === "POST" ? await request.clone().json() : undefined;
    recibidas.push({ metodo: request.method, ruta, ...(cuerpo ? { cuerpo } : {}) });
  });
  server.use(
    http.get("*/api/v1/usage/summary", () =>
      responder(
        fase.aprobada
          ? "resumen-tras-mejora"
          : fase.solicitada
            ? "resumen-tras-solicitar"
            : "resumen-agotado",
      ),
    ),
    http.get("*/api/v1/plans/upgrade-request", () =>
      responder(
        fase.aprobada
          ? "solicitud-aplicada"
          : fase.solicitada
            ? "solicitud-abierta"
            : "solicitud-sin-abrir",
      ),
    ),
    http.get("*/api/v1/plans", () => responder(fase.aprobada ? "planes-tras-mejora" : "planes")),
    http.post("*/api/v1/plans/upgrade-request", () => {
      fase.solicitada = true;
      return responder("solicitar");
    }),
  );
  return { fase, recibidas };
}

// Un recorrido completo del portal (dos inicios de sesión): más que los 5 s por defecto.
it("del aviso de cuota agotada a la mejora aprobada, con lo que responde el backend real", async () => {
  const { fase, recibidas } = backendGrabado();
  const antes = respuestas["resumen-agotado"]!.cuerpo as {
    cola: { tickets: number };
    almacenamiento: { porcentaje: number };
  };

  // 1. Los avisos: cuota agotada con los tickets en cola y almacenamiento al 80 %.
  let usuario = await entrarDesde("/");
  const cuota = await screen.findByTestId("layout.avisos.cuota");
  expect(cuota).toHaveTextContent("Se agotó la cuota de LOC de este ciclo");
  expect(cuota).toHaveTextContent(`Tickets en cola: ${antes.cola.tickets}`);
  expect(texto(screen.getByTestId("layout.avisos.almacenamiento"))).toContain(
    `Usaste el ${antes.almacenamiento.porcentaje} % del almacenamiento de evidencia`,
  );

  // 2. Consumo: el plan, el almacenamiento y la cola.
  await usuario.click(screen.getByRole("link", { name: /Consumo y plan/ }));
  expect(await screen.findByTestId("consumo.cabecera.plan")).toHaveTextContent("starter");
  expect(screen.getByTestId("consumo.almacenamiento.barra")).toHaveAttribute(
    "aria-valuenow",
    String(antes.almacenamiento.porcentaje),
  );
  expect(screen.getByTestId("consumo.cola.resumen")).toHaveTextContent(
    `Tickets en cola por cuota: ${antes.cola.tickets}`,
  );
  expect(screen.queryByTestId("consumo.avisos.solicitud")).toBeNull();

  // 3. Comparar y solicitar Growth, el siguiente al vigente, con un comentario.
  await usuario.click(screen.getByTestId("consumo.cabecera.upgrade"));
  const dialogo = await screen.findByTestId("consumo.planes.dialogo");
  await within(dialogo).findAllByTestId("consumo.planes.plan");
  expect(within(dialogo).getByRole("radio", { name: "Solicitar Growth" })).toBeChecked();
  expect(within(dialogo).getAllByRole("radio")).toHaveLength(2);
  const pedida = solicitudes.find((s) => s.paso === "solicitar")!;
  await usuario.type(
    within(dialogo).getByTestId("consumo.planes.comentario"),
    pedida.cuerpo!.comentario as string,
  );
  await usuario.click(within(dialogo).getByTestId("consumo.planes.enviar"));

  // 4. La respuesta y la solicitud abierta, prioritaria por la cuota agotada.
  expect(await screen.findByTestId("consumo.avisos.mensaje")).toHaveTextContent(
    (respuestas["solicitar"]!.cuerpo as { mensaje: string }).mensaje,
  );
  const abierta = await screen.findByTestId("consumo.avisos.solicitud");
  expect(abierta).toHaveTextContent("Solicitud de mejora a Growth en curso");
  expect(abierta).toHaveTextContent("Prioritaria: cuota agotada");
  expect(recibidas.filter((r) => r.metodo === "POST")).toEqual([
    { metodo: "POST", ruta: pedida.ruta, cuerpo: pedida.cuerpo },
  ]);

  // 5. El equipo comercial la aprueba; al volver a entrar, el plan nuevo.
  fase.aprobada = true;
  cleanup();
  usuario = await entrarDesde("/consumo");
  expect(await screen.findByTestId("consumo.cabecera.plan")).toHaveTextContent("growth");
  expect(screen.queryByTestId("consumo.avisos.solicitud")).toBeNull();
  expect(screen.queryByTestId("layout.avisos.cuota")).toBeNull();
  expect(screen.queryByTestId("layout.avisos.almacenamiento")).toBeNull();
  // Los tickets en cola siguen esperando la renovación: aplicar la mejora no los libera.
  expect(screen.getByTestId("layout.avisos.cola")).toHaveTextContent(
    `Hay tickets en cola por cuota: ${antes.cola.tickets}`,
  );

  await usuario.click(screen.getByTestId("consumo.cabecera.upgrade"));
  const comparador = await screen.findByTestId("consumo.planes.dialogo");
  await within(comparador).findAllByTestId("consumo.planes.plan");
  expect(
    within(comparador).getByRole("heading", { name: "Growth" }).parentElement,
  ).toHaveTextContent("Tu plan");
  expect(within(comparador).getAllByRole("radio")).toHaveLength(1);

  // 6. El portal pidió cada solicitud del archivo (salvo la aprobación, que no es suya).
  await waitFor(() =>
    expect(new Set(recibidas.map((r) => `${r.metodo} ${r.ruta}`))).toEqual(
      new Set(solicitudes.filter((s) => !s.interna).map((s) => `${s.metodo} ${s.ruta}`)),
    ),
  );
}, 15_000);
