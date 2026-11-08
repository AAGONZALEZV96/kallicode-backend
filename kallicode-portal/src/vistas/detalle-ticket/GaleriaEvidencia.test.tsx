/**
 * Galería de evidencia QA (KC-PT-05-2): capturas, videos y trazas con enlace firmado,
 * evidencias sin enlace (hoy el backend no lo envía), enlaces vencidos y refrescos.
 */
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { fijarSesion } from "../../api/client";
import { crearClienteConsultas } from "../../api/consultas";
import type { Evidencia } from "../../api/tipos";
import { I18nProvider } from "../../estado/i18n";
import { BLOB, evidencia } from "../../mocks/datos";
import { server } from "../../mocks/server";
import TabEvidencia from "./TabEvidencia";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

const RUTA = "*/api/v1/tickets/KC-1001/evidence";

function abrir(cliente: QueryClient = crearClienteConsultas()) {
  render(
    <I18nProvider>
      <QueryClientProvider client={cliente}>
        <MemoryRouter>
          <TabEvidencia numero="KC-1001" />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
  return { usuario: userEvent.setup(), cliente };
}

const id = (elemento: string) => screen.getByTestId(`detalle-ticket.${elemento}`);
const ids = (elemento: string) => screen.queryAllByTestId(`detalle-ticket.${elemento}`);
const galeria = () => screen.findByTestId("detalle-ticket.evidencia.galeria");

/** Responde con la evidencia del doble, con los archivos de C2 cambiados por `evidencias`. */
function conEvidencias(
  evidencias: Evidencia["matriz"][number]["evidencias"],
  cambios: Partial<Evidencia> = {},
) {
  const matriz = evidencia.matriz.map((fila) =>
    fila.criterio_id === "C2" ? { ...fila, evidencias } : fila,
  );
  return HttpResponse.json({ ...evidencia, matriz, ...cambios });
}

/** Cuenta los GET de la evidencia. */
function contarPeticiones() {
  const cuenta = { n: 0 };
  const anotar = ({ request }: { request: Request }) => {
    if (new URL(request.url).pathname === "/api/v1/tickets/KC-1001/evidence") cuenta.n++;
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return cuenta;
}

const url = (archivo: string, firma = "msw") =>
  `${BLOB}/evidencia/tk_1001/r1/${archivo}?sig=${firma}`;

describe("galería con enlaces firmados", () => {
  it("agrupa por criterio la captura, el video y la traza de lo que falló", async () => {
    abrir();
    const contenido = within(await galeria());

    // C1 pasó sin archivos: solo aparece C2.
    expect(contenido.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual([
      "C2",
    ]);
    expect(ids("evidencia.item").map((li) => li.firstChild?.textContent)).toEqual([
      "Captura",
      "Video",
      "Traza de Playwright",
    ]);

    const miniatura = id("evidencia.miniatura");
    expect(miniatura).toHaveAttribute("href", url("c2.png"));
    expect(miniatura).toHaveAttribute("target", "_blank");
    expect(miniatura).toHaveAttribute("rel", "noopener noreferrer");
    expect(
      within(miniatura).getByRole("img", { name: "Captura de pantalla de C2" }),
    ).toHaveAttribute("src", url("c2.png"));

    expect(ids("evidencia.descargar").map((a) => a.getAttribute("href"))).toEqual([
      url("c2.png"),
      url("c2.webm"),
      url("c2_trace.zip"),
    ]);
    expect(
      screen.getByRole("link", { name: "Descargar Traza de Playwright de C2" }),
    ).toBeInTheDocument();
    expect(contenido.getByText(/npx playwright show-trace/)).toBeInTheDocument();
    expect(ids("evidencia.no-disponible")).toHaveLength(0);
  });

  it("el video se ve en un diálogo: Tab llega a sus controles y al cerrar el foco vuelve", async () => {
    const { usuario } = abrir();
    await galeria();
    const verVideo = screen.getByRole("button", { name: "Ver video de C2" });

    await usuario.click(verVideo);

    const dialogo = screen.getByRole("dialog", { name: "Video de C2" });
    const reproductor = within(dialogo).getByTestId("detalle-ticket.video.reproductor");
    expect(reproductor).toHaveAttribute("src", url("c2.webm"));
    expect(reproductor).toHaveAttribute("controls");
    expect(reproductor).toHaveFocus();
    expect(within(dialogo).getByTestId("detalle-ticket.video.descargar")).toHaveAttribute(
      "href",
      url("c2.webm"),
    );

    // Desde el último control, Tab vuelve al video (antes no estaba entre los enfocables).
    within(dialogo).getByTestId("detalle-ticket.video.cerrar").focus();
    await usuario.tab();
    expect(reproductor).toHaveFocus();

    await usuario.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(verVideo).toHaveFocus();
  });

  it("al refrescar con firmas nuevas mantiene los enlaces vigentes", async () => {
    const peticiones = contarPeticiones();
    const { cliente } = abrir();
    await galeria();
    server.use(
      http.get(RUTA, () =>
        conEvidencias(
          [
            {
              tipo: "screenshot",
              blob_ref: "evidencia/tk_1001/r1/c2.png",
              download_url: url("c2.png", "nueva"),
              expira_en: "2099-01-01T00:10:00Z",
            },
          ],
          // Un cambio visible para saber que la pantalla ya pintó la respuesta nueva.
          { regresion: { ...evidencia.regresion, cliente_ok: 39 } },
        ),
      ),
    );

    await cliente.refetchQueries();

    expect(
      await screen.findByText("suite del cliente: 39/40", { exact: false }),
    ).toBeInTheDocument();
    expect(peticiones.n).toBe(2);
    expect(screen.getByRole("img", { name: "Captura de pantalla de C2" })).toHaveAttribute(
      "src",
      url("c2.png"),
    );
  });
});

describe("evidencia sin enlace o con enlace inválido", () => {
  it("sin download_url (como responde hoy el backend) la lista como no disponible", async () => {
    server.use(
      http.get(RUTA, () =>
        conEvidencias([
          { tipo: "screenshot", blob_ref: "evidencia/c2.png" },
          { tipo: "video", blob_ref: "evidencia/c2.webm" },
        ]),
      ),
    );
    abrir();
    await galeria();

    expect(ids("evidencia.no-disponible").map((e) => e.textContent)).toEqual([
      "Descarga aún no disponible.",
      "Descarga aún no disponible.",
    ]);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("si ningún criterio trae archivos lo dice", async () => {
    server.use(http.get(RUTA, () => conEvidencias([])));
    abrir();

    expect(await screen.findByTestId("detalle-ticket.evidencia.galeria-vacia")).toHaveTextContent(
      "Esta corrida no guardó capturas, videos ni trazas.",
    );
  });

  it("un download_url que no es http(s) se rechaza y no se pinta", async () => {
    const consola = vi.spyOn(console, "error").mockImplementation(() => {});
    onTestFinished(() => consola.mockRestore());
    server.use(
      http.get(RUTA, () =>
        conEvidencias([{ tipo: "screenshot", blob_ref: "x", download_url: "javascript:alert(1)" }]),
      ),
    );
    abrir();

    expect(await screen.findByRole("alert")).toHaveTextContent(/formato esperado/);
    expect(screen.queryByTestId("detalle-ticket.evidencia.galeria")).not.toBeInTheDocument();
  });
});

describe("enlace vencido", () => {
  it("al usarlo no abre nada: pide la evidencia de nuevo y avisa hasta que llega", async () => {
    const vencida = {
      tipo: "video",
      blob_ref: "evidencia/tk_1001/r1/c2.webm",
      download_url: url("c2.webm", "vieja"),
      expira_en: "2020-01-01T00:00:00Z",
    };
    let responder: (() => void) | undefined;
    let llamadas = 0;
    server.use(
      http.get(RUTA, async () => {
        if (++llamadas === 1) return conEvidencias([vencida]);
        // La renovación queda en espera para poder ver el aviso.
        await new Promise<void>((r) => (responder = r));
        return conEvidencias([
          { ...vencida, download_url: url("c2.webm", "nueva"), expira_en: "2099-01-01T00:00:00Z" },
        ]);
      }),
    );
    const { usuario } = abrir();
    await galeria();

    await usuario.click(screen.getByRole("button", { name: "Ver video de C2" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const abrioEnlace = fireEvent.click(id("evidencia.descargar"));
    expect(abrioEnlace).toBe(false);

    expect(await screen.findByRole("status")).toHaveTextContent("El enlace venció");
    await waitFor(() => expect(llamadas).toBe(2));
    responder!();

    await waitFor(() =>
      expect(id("evidencia.descargar")).toHaveAttribute("href", url("c2.webm", "nueva")),
    );
    expect(screen.queryByTestId("detalle-ticket.evidencia.renovando")).not.toBeInTheDocument();
  });
});
