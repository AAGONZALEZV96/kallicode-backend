/**
 * Exportaciones de auditoría (KC-PT-08-3), en el portal completo: CSV con los filtros del
 * explorador y, en el expediente, PDF y CSV del ticket; sondeo del estado y descarga.
 * El doble responde «procesando» en la 1.ª consulta y «lista» con su URL en la siguiente.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { perfilOwner } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde } from "../test/portal";

/** Cuerpos de cada POST /audit/exports. */
function pedidos() {
  const cuerpos: unknown[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (request.method === "POST" && new URL(request.url).pathname === "/api/v1/audit/exports") {
      cuerpos.push(await request.clone().json());
    }
  });
  return cuerpos;
}

async function abrir(ruta: string, rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde(ruta);
  await screen.findByTestId(
    ruta.startsWith("/auditoria/") ? "expediente.vista.raiz" : "auditoria.vista.raiz",
  );
  return usuario;
}

const t = (id: string) => screen.getByTestId(id);

describe("explorador: CSV con los filtros aplicados", () => {
  it("manda los filtros (sin orden ni página), sondea y ofrece la descarga", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir(
      "/auditoria?actor=agente&q=deploy&ticket=KC-1001&desde=2026-11-01&hasta=2026-11-20&orden=antiguos&page=2",
    );

    await usuario.click(t("auditoria.exportacion.csv"));

    expect(await screen.findByText("Preparando la exportación…")).toBeInTheDocument();
    expect(t("auditoria.exportacion.csv")).toHaveAttribute("aria-disabled", "true");
    const enlace = await screen.findByTestId(
      "auditoria.exportacion.descargar",
      {},
      { timeout: 5000 },
    );
    expect(enlace).toHaveTextContent("Descargar CSV");
    expect(enlace).toHaveAttribute(
      "href",
      "https://almacen.kallicode.test/exports/exp_1.csv?sig=prueba",
    );
    expect(enlace).toHaveAttribute("rel", "noopener noreferrer");
    expect(t("auditoria.exportacion.estado")).toHaveTextContent(
      /Exportación lista: Descargar CSV \(el enlace vence el .+\)/,
    );
    expect(t("auditoria.exportacion.csv")).toHaveAttribute("aria-disabled", "false");
    expect(cuerpos).toEqual([
      {
        formato: "csv",
        ticket_id: "KC-1001",
        filtros: {
          actor_tipo: "agente",
          q: "deploy",
          desde: new Date("2026-11-01T00:00:00").toISOString(),
          hasta: new Date("2026-11-20T23:59:59.999").toISOString(),
        },
      },
    ]);
  });

  it("sin filtros manda solo el formato", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria");

    await usuario.click(t("auditoria.exportacion.csv"));

    await waitFor(() => expect(cuerpos).toEqual([{ formato: "csv" }]));
  });

  it("mientras se prepara, otro clic no pide otra exportación", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria");

    await usuario.click(t("auditoria.exportacion.csv"));
    await screen.findByText("Preparando la exportación…");
    await usuario.click(t("auditoria.exportacion.csv"));

    expect(t("auditoria.exportacion.csv")).toHaveFocus();
    expect(cuerpos).toHaveLength(1);
  });

  it("con más de 92 días la lista no se consulta, pero se puede exportar", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria?desde=2026-01-01&hasta=2026-06-30");
    expect(t("auditoria.filtros.error")).toHaveTextContent("usa la exportación");

    await usuario.click(t("auditoria.exportacion.csv"));

    await waitFor(() =>
      expect(cuerpos).toEqual([
        {
          formato: "csv",
          filtros: {
            desde: new Date("2026-01-01T00:00:00").toISOString(),
            hasta: new Date("2026-06-30T23:59:59.999").toISOString(),
          },
        },
      ]),
    );
  });

  it("con el rango invertido no se exporta, y dice por qué", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria?desde=2026-11-20&hasta=2026-11-01");
    const boton = t("auditoria.exportacion.csv");

    await usuario.click(boton);

    expect(boton).toHaveAttribute("aria-disabled", "true");
    expect(boton).toHaveAccessibleDescription("La fecha «hasta» es anterior a «desde».");
    expect(cuerpos).toEqual([]);
  });
});

describe("expediente: PDF y CSV del ticket", () => {
  it("el PDF pide el expediente del ticket por su número y ofrece descargarlo", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria/KC-1001");

    await usuario.click(t("expediente.exportacion.pdf"));

    const enlace = await screen.findByTestId(
      "expediente.exportacion.descargar",
      {},
      { timeout: 5000 },
    );
    expect(enlace).toHaveTextContent("Descargar PDF");
    expect(enlace.getAttribute("href")).toMatch(/\.pdf\?sig=prueba$/);
    expect(cuerpos).toEqual([{ formato: "pdf", ticket_id: "KC-1001" }]);
  });

  it("el CSV del expediente lleva solo el ticket", async () => {
    const cuerpos = pedidos();
    const usuario = await abrir("/auditoria/KC-1001");

    await usuario.click(t("expediente.exportacion.csv"));

    await waitFor(() => expect(cuerpos).toEqual([{ formato: "csv", ticket_id: "KC-1001" }]));
  });

  it.each<Rol>(["admin", "architect"])("el %s exporta", async (rol) => {
    await abrir("/auditoria/KC-1001", rol);
    expect(t("expediente.exportacion.pdf")).toBeInTheDocument();
  });

  it("el approver ve el expediente pero no exporta (el backend respondería 403)", async () => {
    await abrir("/auditoria/KC-1001", "approver");
    expect(screen.queryByTestId("expediente.exportacion.pdf")).not.toBeInTheDocument();
  });
});

describe("errores", () => {
  it("si la exportación falla lo dice, y se puede pedir de nuevo", async () => {
    server.use(
      http.get("*/api/v1/audit/exports/:id", ({ params }) =>
        HttpResponse.json({ export_id: params.id, estado: "fallida", completado_en: null }),
      ),
    );
    const usuario = await abrir("/auditoria");

    await usuario.click(t("auditoria.exportacion.csv"));

    expect(
      await screen.findByText("No se pudo generar la exportación. Inténtalo de nuevo."),
    ).toBeInTheDocument();
    expect(t("auditoria.exportacion.csv")).toHaveAttribute("aria-disabled", "false");
    expect(screen.queryByTestId("auditoria.exportacion.descargar")).not.toBeInTheDocument();
  });

  it("con 3 exportaciones en curso, el 429 sale traducido", async () => {
    server.use(
      http.post("*/api/v1/audit/exports", () =>
        sobreError(429, "EXPORTS_CONCURRENTES", "Ya hay 3 exportaciones en curso."),
      ),
    );
    const usuario = await abrir("/auditoria");

    await usuario.click(t("auditoria.exportacion.csv"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ya hay 3 exportaciones en curso; espera a que terminen.",
    );
    expect(t("auditoria.exportacion.csv")).toHaveAttribute("aria-disabled", "false");
  });

  it("un enlace que no es http(s) no se ofrece", async () => {
    server.use(
      http.get("*/api/v1/audit/exports/:id", ({ params }) =>
        HttpResponse.json({
          export_id: params.id,
          estado: "lista",
          completado_en: "2026-11-21T12:00:00Z",
          download_url: "javascript:alert(1)",
        }),
      ),
    );
    const usuario = await abrir("/auditoria");

    await usuario.click(t("auditoria.exportacion.csv"));

    expect(
      await within(t("auditoria.exportacion.estado")).findByText(
        /el enlace de descarga no es válido/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("auditoria.exportacion.descargar")).not.toBeInTheDocument();
  });
});
