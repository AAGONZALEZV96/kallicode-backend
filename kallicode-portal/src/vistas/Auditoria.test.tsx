/**
 * Explorador de auditoría con los filtros en la URL (KC-PT-08-5, cierra PT-F16), en el portal
 * completo: la URL arma la consulta, los controles escriben la URL, y al volver del
 * expediente el listado sigue filtrado.
 */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { eventosAuditoria } from "../mocks/datos";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";
import { ESPERA_DEBOUNCE_MS } from "../useDebounce";

const id = (e: string) => screen.getByTestId(`auditoria.${e}`);
const sinId = (e: string) => screen.queryByTestId(`auditoria.${e}`);

/** Parámetros de cada GET /audit/events. */
function consultas() {
  const enviadas: Record<string, string>[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/v1/audit/events") {
      enviadas.push(Object.fromEntries(url.searchParams));
    }
  });
  return enviadas;
}

async function abrir(ruta = "/auditoria") {
  const usuario = await entrarDesde(ruta);
  await screen.findByTestId("auditoria.vista.raiz");
  return usuario;
}

const ultima = (enviadas: Record<string, string>[]) => enviadas[enviadas.length - 1];
const ruta = () => new URLSearchParams(rutaActual()!.split("?")[1] ?? "");

describe("la URL arma la consulta", () => {
  it("un enlace con filtros los aplica y los muestra en los controles", async () => {
    const enviadas = consultas();
    await abrir(
      "/auditoria?q=gate&actor=agente&ticket=KC-1001&etapa=build&desde=2026-11-01&hasta=2026-11-20&orden=antiguos&page=2",
    );

    await waitFor(() =>
      expect(ultima(enviadas)).toEqual({
        page: "2",
        page_size: "50",
        q: "gate",
        actor_tipo: "agente",
        ticket_id: "KC-1001",
        etapa: "build",
        desde: new Date("2026-11-01T00:00:00").toISOString(),
        hasta: new Date("2026-11-20T23:59:59.999").toISOString(),
        sort: "creado_en",
      }),
    );
    expect(id("filtros.busqueda")).toHaveValue("gate");
    expect(id("filtros.ticket")).toHaveValue("KC-1001");
    expect(id("filtros.actor")).toHaveValue("agente");
    expect(id("filtros.etapa")).toHaveValue("build");
    expect(id("filtros.desde")).toHaveValue("2026-11-01");
    expect(id("filtros.hasta")).toHaveValue("2026-11-20");
    expect(id("filtros.orden")).toHaveValue("antiguos");
  });

  it("los valores inválidos de la URL se ignoran en vez de provocar un 422", async () => {
    const enviadas = consultas();
    await abrir("/auditoria?actor=root&etapa=borrar&desde=ayer&orden=raro");

    await waitFor(() => expect(ultima(enviadas)).toEqual({ page: "1", page_size: "50" }));
    expect(id("filtros.actor")).toHaveValue("");
    expect(id("filtros.etapa")).toHaveValue("");
    expect(id("filtros.desde")).toHaveValue("");
    expect(sinId("filtros.limpiar")).not.toBeInTheDocument();
  });
});

describe("los controles escriben la URL", () => {
  it("cada filtro va a la URL y vuelve a la página 1", async () => {
    const enviadas = consultas();
    const usuario = await abrir("/auditoria?page=3");

    await usuario.selectOptions(id("filtros.etapa"), "deploy");
    await waitFor(() => expect(ruta().get("etapa")).toBe("deploy"));
    expect(ruta().has("page")).toBe(false);

    await usuario.selectOptions(id("filtros.actor"), "humano");
    await usuario.selectOptions(id("filtros.orden"), "actor");
    await waitFor(() =>
      expect(ultima(enviadas)).toMatchObject({
        page: "1",
        etapa: "deploy",
        actor_tipo: "humano",
        sort: "actor_tipo",
      }),
    );
    expect(rutaActual()).toBe("/auditoria?etapa=deploy&actor=humano&orden=actor");
  });

  it("los textos llegan a la URL al dejar de escribir, una sola consulta", async () => {
    const enviadas = consultas();
    const usuario = await abrir();

    await usuario.type(id("filtros.ticket"), "KC-1001");

    await waitFor(() => expect(ruta().get("ticket")).toBe("KC-1001"));
    await waitFor(() => expect(ultima(enviadas)?.ticket_id).toBe("KC-1001"));
    expect(enviadas.filter((p) => p.ticket_id).map((p) => p.ticket_id)).toEqual(["KC-1001"]);
  });

  it("las fechas de un día a otro van como días completos", async () => {
    const enviadas = consultas();
    const usuario = await abrir();

    await usuario.type(id("filtros.desde"), "2026-11-10");
    await usuario.type(id("filtros.hasta"), "2026-11-12");

    await waitFor(() =>
      expect(ultima(enviadas)).toMatchObject({
        desde: new Date("2026-11-10T00:00:00").toISOString(),
        hasta: new Date("2026-11-12T23:59:59.999").toISOString(),
      }),
    );
    expect(rutaActual()).toBe("/auditoria?desde=2026-11-10&hasta=2026-11-12");
  });
});

describe("rango de fechas", () => {
  it.each([
    ["2026-11-20", "2026-11-01", "La fecha «hasta» es anterior a «desde»."],
    [
      "2026-01-01",
      "2026-06-01",
      "El rango máximo es de 92 días; para períodos más largos usa la exportación.",
    ],
  ])("desde %s hasta %s: avisa junto a las fechas y no consulta", async (desde, hasta, mensaje) => {
    const enviadas = consultas();
    await abrir(`/auditoria?desde=${desde}&hasta=${hasta}`);

    expect(await screen.findByTestId("auditoria.filtros.error")).toHaveTextContent(mensaje);
    expect(id("filtros.hasta")).toHaveAttribute("aria-invalid", "true");
    expect(id("filtros.hasta")).toHaveAccessibleDescription(mensaje);
    expect(sinId("lista.tabla")).not.toBeInTheDocument();
    expect(enviadas.filter((p) => p.desde)).toEqual([]);
  });

  it("al corregir el rango, consulta", async () => {
    const enviadas = consultas();
    const usuario = await abrir("/auditoria?desde=2026-11-20&hasta=2026-11-01");
    await screen.findByTestId("auditoria.filtros.error");

    await usuario.clear(id("filtros.hasta"));
    await usuario.type(id("filtros.hasta"), "2026-11-25");

    await screen.findByTestId("auditoria.lista.tabla");
    expect(sinId("filtros.error")).not.toBeInTheDocument();
    expect(ultima(enviadas)?.hasta).toBe(new Date("2026-11-25T23:59:59.999").toISOString());
  });
});

describe("limpiar y volver", () => {
  it("«Limpiar filtros» deja la URL limpia, vacía los campos y lleva el foco a la búsqueda", async () => {
    const enviadas = consultas();
    const usuario = await abrir("/auditoria?q=gate&ticket=KC-1001&etapa=qa&page=2");
    await waitFor(() => expect(ultima(enviadas)?.q).toBe("gate"));
    const antes = enviadas.length;

    await usuario.click(id("filtros.limpiar"));

    await waitFor(() => expect(rutaActual()).toBe("/auditoria"));
    await waitFor(() => expect(id("filtros.busqueda")).toHaveValue(""));
    expect(id("filtros.ticket")).toHaveValue("");
    expect(id("filtros.etapa")).toHaveValue("");
    expect(id("filtros.busqueda")).toHaveFocus();
    expect(sinId("filtros.limpiar")).not.toBeInTheDocument();

    // Pasado el debounce, ningún campo vuelve a escribir en la URL lo que tenía antes.
    await new Promise((r) => setTimeout(r, ESPERA_DEBOUNCE_MS + 150));
    expect(rutaActual()).toBe("/auditoria");
    expect(id("filtros.busqueda")).toHaveValue("");
    // Ni siquiera por un momento: ninguna consulta posterior lleva los filtros anteriores.
    const despues = enviadas.slice(antes);
    expect(despues.length).toBeGreaterThan(0);
    expect(despues.filter((p) => p.q || p.ticket_id || p.etapa)).toEqual([]);
  });

  it("al volver del expediente el listado sigue con los mismos filtros", async () => {
    const usuario = await abrir("/auditoria?actor=humano&etapa=triage");
    await screen.findByText("KC-1001");

    await usuario.click(screen.getByRole("link", { name: "KC-1001" }));
    await screen.findByTestId("expediente.vista.raiz");
    await usuario.click(screen.getByTestId("expediente.cabecera.volver"));

    await screen.findByTestId("auditoria.lista.tabla");
    expect(rutaActual()).toBe("/auditoria?actor=humano&etapa=triage");
    expect(id("filtros.actor")).toHaveValue("humano");
  });

  it("al entrar al expediente sin pasar por el explorador, «volver» lleva al explorador sin filtros", async () => {
    const usuario = await entrarDesde("/auditoria/KC-1001");
    await screen.findByTestId("expediente.vista.raiz");

    await usuario.click(screen.getByTestId("expediente.cabecera.volver"));

    await waitFor(() => expect(rutaActual()).toBe("/auditoria"));
  });
});

it("sin resultados lo dice", async () => {
  server.use(
    http.get("*/api/v1/audit/events", () =>
      HttpResponse.json({ ...eventosAuditoria, items: [], total: 0 }),
    ),
  );
  await abrir("/auditoria?ticket=KC-9999");

  expect(await screen.findByTestId("auditoria.lista.vacio")).toHaveTextContent(
    "No hay eventos con estos filtros.",
  );
});
