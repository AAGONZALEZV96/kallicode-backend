/**
 * Recorrido del expediente de auditoría (KC-PT-08-6), en el portal completo: del explorador
 * filtrado al expediente, verificar los sellos, inspeccionar un paso LLM y generar y descargar
 * el PDF.
 *
 * El backend responde con lo grabado del backend real en
 * apps/api/tests/integracion/expediente_desde_portal.json, que test_expediente_desde_portal.py
 * (al lado) vuelve a pedir en cada corrida y compara con lo grabado. Aquí se comprueba que el
 * portal muestra esas respuestas y manda exactamente las solicitudes del archivo, en orden: si
 * una de las dos partes cambia lo que manda o lo que responde, falla una de las dos pruebas.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";

import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

interface Solicitud {
  paso: string;
  metodo: string;
  ruta?: string;
  url?: string;
  consulta?: Record<string, string>;
  cuerpo?: Record<string, unknown>;
}
interface Contrato {
  solicitudes: Solicitud[];
  valores: Record<string, string>;
  respuestas: Record<string, { status: number; cuerpo: unknown }>;
}

const CONTRATO: Contrato = JSON.parse(
  readFileSync(resolve("../apps/api/tests/integracion/expediente_desde_portal.json"), "utf-8"),
);
const { valores, respuestas } = CONTRATO;
const rellenar = (texto: string) => texto.replace(/\{(\w+)\}/g, (_, k: string) => valores[k]!);
const rellenarTodo = <T,>(v: T): T => JSON.parse(rellenar(JSON.stringify(v)));
const responder = (paso: string) =>
  HttpResponse.json(respuestas[paso]!.cuerpo as object, { status: respuestas[paso]!.status });

/** Lo que el portal pide al backend de auditoría y al Blob, en orden y sin marcadores. */
function backendGrabado() {
  const recibidas: Omit<Solicitud, "paso">[] = [];
  server.events.on("request:start", async ({ request }) => {
    const u = new URL(request.url);
    const alBlob =
      request.url.split("?")[0] === valores.entrada_url!.split("?")[0] ||
      request.url.split("?")[0] === valores.salida_url!.split("?")[0];
    if (alBlob) {
      recibidas.push({ metodo: request.method, url: request.url });
    } else if (u.pathname.startsWith("/api/v1/audit/")) {
      const consulta = Object.fromEntries(u.searchParams);
      const texto = request.method === "GET" ? "" : await request.clone().text();
      recibidas.push({
        metodo: request.method,
        ruta: u.pathname,
        ...(Object.keys(consulta).length ? { consulta } : {}),
        ...(texto ? { cuerpo: JSON.parse(texto) } : {}),
      });
    }
  });

  let expedientes = 0;
  server.use(
    http.get("*/api/v1/audit/events", () => responder("explorador")),
    http.get("*/api/v1/audit/tickets/:numero", () =>
      responder(++expedientes === 1 ? "expediente" : "expediente-tras-verificar"),
    ),
    http.get("*/api/v1/audit/integrity", () => responder("verificar")),
    http.get("*/api/v1/audit/steps/:id", () => responder("paso")),
    http.get(valores.entrada_url!.split("?")[0]!, () => responder("traza-entrada")),
    http.get(valores.salida_url!.split("?")[0]!, () => responder("traza-salida")),
    http.post("*/api/v1/audit/exports", () => responder("exportar")),
    http.get("*/api/v1/audit/exports/:id", () => responder("estado-exportacion")),
  );
  return recibidas;
}

const t = (id: string) => screen.getByTestId(id);

it("del explorador al PDF del expediente, con lo que responde el backend real", async () => {
  const recibidas = backendGrabado();
  const numero = valores.numero!;
  const usuario = await entrarDesde(`/auditoria?ticket=${numero}&actor=agente`);

  // 1. Explorador filtrado: los eventos de agente del ticket.
  const tabla = await screen.findByTestId("auditoria.lista.tabla");
  const eventos = (respuestas.explorador!.cuerpo as { items: { evento: string }[] }).items;
  expect(within(tabla).getAllByTestId("auditoria.lista.fila")).toHaveLength(eventos.length);
  await usuario.click(within(tabla).getAllByRole("link", { name: numero })[0]!);

  // 2. Expediente: integridad del tramo con lo que verificó el backend.
  await screen.findByTestId("expediente.vista.raiz");
  expect(await screen.findByText(`Expediente de auditoría · ${numero}`)).toBeInTheDocument();
  expect(t("expediente.integridad.estado")).toHaveTextContent("✓ Cadena de hashes íntegra");
  expect(t("expediente.integridad.tramo")).toHaveTextContent("eventos verificados: 3");

  // 3. Verificar los sellos de nuevo.
  await usuario.click(t("expediente.integridad.verificar"));
  await waitFor(() =>
    expect(t("expediente.integridad.resultado")).toHaveTextContent("eventos: 3 · cadena íntegra"),
  );

  // 4. Inspeccionar el paso LLM: datos, y la entrada y la salida leídas del Blob.
  await usuario.click(screen.getByRole("button", { name: "Inspeccionar triage.classify" }));
  const datos = await screen.findByTestId("expediente.paso.datos");
  expect(datos).toHaveTextContent("triage.classify · v3");
  expect(datos).toHaveTextContent("0.87");
  await waitFor(() =>
    expect(t("expediente.paso.entrada")).toHaveTextContent(
      '"titulo": "La exportación mensual falla con timeout"',
    ),
  );
  await waitFor(() => expect(t("expediente.paso.salida")).toHaveTextContent('"prioridad": "alta"'));
  await usuario.click(t("expediente.paso.cerrar"));

  // 5. El diff del build (KC-PT-08-1): el SHA-256 que calculó el backend real coincide con el
  //    que recalcula el navegador sobre el mismo texto.
  const [cambio] = (respuestas.expediente!.cuerpo as { cambios: { archivo: string }[] }).cambios;
  expect(t("expediente.cambios.archivo")).toHaveTextContent(cambio!.archivo);
  expect(screen.getByRole("table", { name: `Diff de ${cambio!.archivo}` })).toBeInTheDocument();
  await waitFor(() =>
    expect(t("expediente.cambios.verificacion")).toHaveTextContent(
      "✓ Coincide con su sello SHA-256",
    ),
  );

  // 6. Generar el PDF y ofrecer la descarga con la URL firmada del backend.
  await usuario.click(t("expediente.exportacion.pdf"));
  const enlace = await screen.findByTestId(
    "expediente.exportacion.descargar",
    {},
    { timeout: 5000 },
  );
  expect(enlace).toHaveAttribute("href", valores.download_url);
  expect(enlace).toHaveTextContent("Descargar PDF");

  // El portal mandó exactamente lo del contrato, en orden.
  expect(recibidas).toEqual(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- el paso solo nombra la solicitud
    CONTRATO.solicitudes.map(({ paso, ...s }) => rellenarTodo(s)),
  );
});
