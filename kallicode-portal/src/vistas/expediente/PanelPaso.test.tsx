/**
 * Panel de inspección de pasos LLM (KC-PT-08-2), en el portal completo: detalle del paso
 * (GET /audit/steps/{id}), trazas leídas de sus URLs firmadas y qué pasa cuando no se pueden leer.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { DetallePaso, Rol } from "../../api/tipos";
import { detallePaso, perfilOwner, TRAZAS } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

async function abrirPanel(rol: Rol = "owner") {
  if (rol !== "owner") {
    server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
  }
  const usuario = await entrarDesde("/auditoria/KC-1001");
  await usuario.click(await screen.findByRole("button", { name: "Inspeccionar triage.classify" }));
  const panel = await screen.findByRole("dialog", { name: "Paso triage.classify" });
  return { usuario, panel };
}

/** Peticiones a una ruta, para contar o mirar sus cabeceras. */
function peticiones(filtro: (url: URL) => boolean) {
  const lista: Request[] = [];
  server.events.on("request:start", ({ request }) => {
    if (filtro(new URL(request.url))) lista.push(request);
  });
  return lista;
}

function responderPaso(cambios: Partial<DetallePaso>) {
  server.use(
    http.get("*/api/v1/audit/steps/:id", () => HttpResponse.json({ ...detallePaso, ...cambios })),
  );
}

const t = (id: string) => screen.getByTestId(id);

describe("panel de inspección", () => {
  it("muestra plantilla, modelo, tier, ejecución y tokens del paso", async () => {
    await abrirPanel();

    const datos = await screen.findByTestId("expediente.paso.datos");
    const fila = (etiqueta: string) =>
      within(datos).getByText(etiqueta).nextElementSibling?.textContent;
    expect(fila("Etapa")).toBe("Triage");
    expect(fila("Plantilla")).toBe("triage.classify · v3");
    expect(fila("Modelo y tier")).toBe("gemini-2.5-flash · flash");
    expect(fila("Confianza")).toBe("0.92");
    expect(fila("Validación")).toBe("esquema_valido");
    expect(fila("Reintentos")).toBe("1");
    // Español: 4 cifras sin separador de miles (RAE/CLDR); desde 5 cifras, con punto.
    expect(fila("Tokens")).toBe("entrada 1520 · salida 320 · total 1840");
    expect(fila("Duración")).toBe("2310 ms");
    // Sin punto final propio: la hora en es-CL ya termina en «p.m.» (quedaba «p.m..»).
    const vencen = screen.getByText(/^Los enlaces a las trazas vencen a las \d{1,2}:\d{2}/);
    expect(vencen.textContent).not.toMatch(/\.\.$|\d\.$/);
  });

  it("muestra la entrada y la salida como JSON con sangría, con el enlace para abrirlas", async () => {
    await abrirPanel();

    await waitFor(() =>
      expect(t("expediente.paso.entrada")).toHaveTextContent(
        '"titulo": "La exportación falla con timeout"',
      ),
    );
    expect(within(t("expediente.paso.entrada")).getByRole("region").textContent).toContain(
      '{\n  "titulo"',
    );
    await waitFor(() =>
      expect(t("expediente.paso.salida")).toHaveTextContent('"prioridad": "alta"'),
    );
    for (const tipo of ["entrada", "salida"]) {
      const enlace = t(`expediente.paso.abrir-${tipo}`);
      expect(enlace).toHaveAttribute("href", `${TRAZAS}/${tipo}.json?sig=prueba`);
      expect(enlace).toHaveAttribute("target", "_blank");
      expect(enlace).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("pide las trazas sin el token del portal", async () => {
    const trazas = peticiones((u) => u.pathname.startsWith("/trazas-llm/"));
    await abrirPanel();

    await waitFor(() => expect(trazas).toHaveLength(2));
    for (const r of trazas) expect(r.headers.get("Authorization")).toBeNull();
  });

  it("si el almacenamiento no deja leerla (CORS), lo dice y deja el enlace", async () => {
    server.use(http.get(`${TRAZAS}/entrada.json`, () => HttpResponse.error()));
    await abrirPanel();

    expect(
      await within(t("expediente.paso.entrada")).findByText(
        /No se puede mostrar aquí: el almacenamiento no permite leerla desde el portal/,
      ),
    ).toBeInTheDocument();
    expect(t("expediente.paso.abrir-entrada")).toBeInTheDocument();
    // La otra traza se lee igual.
    await waitFor(() =>
      expect(t("expediente.paso.salida")).toHaveTextContent('"prioridad": "alta"'),
    );
  });

  it("si el enlace venció (403), pide reabrir el panel", async () => {
    server.use(
      http.get(
        `${TRAZAS}/salida.json`,
        () => new HttpResponse("AuthenticationFailed", { status: 403 }),
      ),
    );
    await abrirPanel();

    expect(
      await within(t("expediente.paso.salida")).findByText(
        "El enlace venció. Cierra el panel y ábrelo de nuevo para pedir otro.",
      ),
    ).toBeInTheDocument();
  });

  it("una traza que no es JSON se muestra tal cual", async () => {
    server.use(http.get(`${TRAZAS}/salida.json`, () => HttpResponse.text("respuesta en texto")));
    await abrirPanel();

    await waitFor(() =>
      expect(within(t("expediente.paso.salida")).getByRole("region")).toHaveTextContent(
        /^respuesta en texto$/,
      ),
    );
  });

  it("sin traza en Blob (o con una URL que no es http/https) no pide nada ni enlaza", async () => {
    responderPaso({ entrada_url: null, salida_url: "javascript:alert(1)", urls_expiran_en: null });
    const trazas = peticiones((u) => u.pathname.startsWith("/trazas-llm/"));
    await abrirPanel();

    await screen.findByTestId("expediente.paso.datos");
    for (const tipo of ["entrada", "salida"]) {
      expect(t(`expediente.paso.${tipo}`)).toHaveTextContent(
        "Este paso no tiene su traza guardada en el almacenamiento.",
      );
      expect(screen.queryByTestId(`expediente.paso.abrir-${tipo}`)).not.toBeInTheDocument();
    }
    expect(screen.queryByText(/vencen a las/)).not.toBeInTheDocument();
    expect(trazas).toHaveLength(0);
  });

  it("si el paso no existe, el error queda en el panel", async () => {
    server.use(
      http.get("*/api/v1/audit/steps/:id", () =>
        sobreError(404, "RECURSO_NO_ENCONTRADO", "El paso no existe."),
      ),
    );
    const { panel } = await abrirPanel();

    expect(await within(panel).findByRole("alert")).toHaveTextContent("RECURSO_NO_ENCONTRADO");
    expect(t("expediente.paso.cerrar")).toBeInTheDocument();
  });

  it("se cierra con el botón o con Escape y el foco vuelve a «Inspeccionar»", async () => {
    const { usuario } = await abrirPanel();
    const boton = screen.getByRole("button", { name: "Inspeccionar triage.classify" });

    await usuario.click(t("expediente.paso.cerrar"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(boton).toHaveFocus();

    await usuario.click(boton);
    await screen.findByRole("dialog");
    await usuario.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(boton).toHaveFocus();
  });

  it("al reabrirlo pide el paso otra vez (URLs nuevas), pero no las trazas ya leídas", async () => {
    const pasos = peticiones((u) => u.pathname === "/api/v1/audit/steps/st_1");
    const trazas = peticiones((u) => u.pathname.startsWith("/trazas-llm/"));
    const { usuario } = await abrirPanel();
    await waitFor(() => expect(trazas).toHaveLength(2));

    await usuario.click(t("expediente.paso.cerrar"));
    await usuario.click(screen.getByRole("button", { name: "Inspeccionar triage.classify" }));
    await waitFor(() => expect(t("expediente.paso.entrada")).toHaveTextContent('"titulo"'));

    await waitFor(() => expect(pasos).toHaveLength(2));
    expect(trazas).toHaveLength(2);
  });
});

describe("quién inspecciona", () => {
  it.each<Rol>(["admin", "architect"])("el %s también", async (rol) => {
    await abrirPanel(rol);
    expect(await screen.findByTestId("expediente.paso.datos")).toBeInTheDocument();
  });

  it("el approver ve los pasos, pero no los inspecciona (el backend respondería 403)", async () => {
    server.use(
      http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol: "approver" })),
    );
    await entrarDesde("/auditoria/KC-1001");

    expect(await screen.findByText("triage.classify")).toBeInTheDocument();
    expect(screen.queryByTestId("expediente.pasos.inspeccionar")).not.toBeInTheDocument();
  });
});
