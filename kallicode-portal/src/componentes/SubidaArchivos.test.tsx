/**
 * Adjuntos al crear un ticket (KC-PT-05-1): subida con URL firmada, arrastrar, pegar,
 * progreso, reglas de tipo, tamaño y cantidad, y cuota de almacenamiento del plan.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ComponentType } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { fijarSesion } from "../api/client";
import { BLOB, sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";
import NuevaFuncionalidad from "../vistas/NuevaFuncionalidad";
import NuevoTicket from "../vistas/NuevoTicket";

beforeEach(() => fijarSesion("acc_1", "ref_1"));

const archivo = (nombre: string, tipo: string, bytes = 2048) =>
  new File([new Uint8Array(bytes)], nombre, { type: tipo });

function abrir(Vista: ComponentType = NuevoTicket) {
  render(
    <Proveedores>
      <MemoryRouter>
        <Vista />
      </MemoryRouter>
    </Proveedores>,
  );
  // Sin filtrar por `accept`: así se prueban también los rechazos.
  return userEvent.setup({ applyAccept: false });
}

const id = (elemento: string, vista = "nuevo-ticket") => screen.getByTestId(`${vista}.${elemento}`);
const filas = () => screen.queryAllByTestId("nuevo-ticket.adjuntos.archivo");

/** Cuerpos de las peticiones POST a `ruta`. */
function cuerposEnviados(ruta: string) {
  const cuerpos: Record<string, unknown>[] = [];
  const anotar = async ({ request }: { request: Request }) => {
    if (request.method === "POST" && new URL(request.url).pathname === ruta)
      cuerpos.push(await request.clone().json());
  };
  server.events.on("request:start", anotar);
  onTestFinished(() => {
    server.events.removeListener("request:start", anotar);
  });
  return cuerpos;
}

async function completarTicket(usuario: ReturnType<typeof userEvent.setup>) {
  await usuario.type(id("formulario.titulo"), "Error al exportar");
  await usuario.type(id("formulario.descripcion"), "El reporte mensual falla");
}

describe("subida de un adjunto", () => {
  it("pide la URL firmada, sube el archivo directo al blob y crea el ticket con su id", async () => {
    const presign = cuerposEnviados("/api/v1/tickets/attachments");
    const tickets = cuerposEnviados("/api/v1/tickets");
    let put: Request | undefined;
    server.use(
      http.put(`${BLOB}/*`, ({ request }) => {
        put = request;
        return new HttpResponse(null, { status: 201 });
      }),
    );
    const usuario = abrir();

    await usuario.upload(id("adjuntos.entrada"), archivo("captura.png", "image/png"));

    expect(await screen.findByText(/Subido/)).toBeInTheDocument();
    expect(presign).toEqual([
      { nombre_archivo: "captura.png", content_type: "image/png", tamano_bytes: 2048 },
    ]);
    expect(put?.url).toContain(`${BLOB}/evidencia/att_captura.png/`);
    expect(put?.headers.get("x-ms-blob-type")).toBe("BlockBlob");
    expect(put?.headers.get("content-type")).toBe("image/png");
    // La credencial del blob es la firma de la URL: el token de sesión no sale de la API.
    expect(put?.headers.get("authorization")).toBeNull();

    await completarTicket(usuario);
    await usuario.click(id("formulario.enviar"));

    await waitFor(() => expect(tickets).toHaveLength(1));
    expect(tickets[0]).toMatchObject({ adjuntos: ["att_captura.png"] });
  });

  it("soltar archivos en la zona los sube", async () => {
    abrir();

    fireEvent.drop(id("adjuntos.zona"), {
      dataTransfer: { files: [archivo("a.png", "image/png"), archivo("b.pdf", "application/pdf")] },
    });

    await waitFor(() => expect(screen.getAllByText(/Subido/)).toHaveLength(2));
  });

  it("pegar una captura en el formulario la adjunta; pegar texto no cambia nada", async () => {
    abrir();
    const descripcion = id("formulario.descripcion");

    const textoNormal = fireEvent.paste(descripcion, { clipboardData: { files: [] } });
    expect(textoNormal).toBe(true);
    expect(filas()).toHaveLength(0);

    fireEvent.paste(descripcion, {
      clipboardData: { files: [archivo("pegada.png", "image/png")] },
    });

    expect(await screen.findByText(/Subido/)).toBeInTheDocument();
    expect(within(filas()[0]!).getByText("pegada.png")).toBeInTheDocument();
  });

  it("también en Nueva funcionalidad: la funcionalidad se crea con los documentos", async () => {
    const features = cuerposEnviados("/api/v1/tickets/features");
    const usuario = abrir(NuevaFuncionalidad);
    const vista = "nueva-funcionalidad";

    await usuario.upload(id("adjuntos.entrada", vista), archivo("spec.pdf", "application/pdf"));
    await screen.findByText(/Subido/);
    await usuario.type(id("formulario.nombre", vista), "Exportar a Excel");
    await usuario.type(id("formulario.objetivo", vista), "Que finanzas descargue el reporte");
    await usuario.click(id("formulario.enviar", vista));

    await waitFor(() => expect(features).toHaveLength(1));
    expect(features[0]).toMatchObject({ adjuntos: ["att_spec.pdf"] });
  });
});

describe("control de cuota y límites", () => {
  it("rechaza en el portal el tipo no permitido, el archivo vacío y los de más de 25 MB", async () => {
    const presign = cuerposEnviados("/api/v1/tickets/attachments");
    abrir();
    const grande = archivo("video.mp4", "video/mp4");
    Object.defineProperty(grande, "size", { value: 30 * 1024 * 1024 });

    fireEvent.drop(id("adjuntos.zona"), {
      dataTransfer: {
        files: [
          archivo("virus.exe", "application/x-msdownload"),
          archivo("vacio.png", "image/png", 0),
          grande,
        ],
      },
    });

    const errores = await screen.findAllByTestId("nuevo-ticket.adjuntos.error");
    expect(errores.map((e) => e.textContent)).toEqual([
      "Tipo de archivo no permitido.",
      "El archivo está vacío.",
      "El archivo supera el máximo de 25 MB.",
    ]);
    expect(presign).toHaveLength(0);
  });

  it("no deja adjuntar más de 10 archivos", async () => {
    const presign = cuerposEnviados("/api/v1/tickets/attachments");
    abrir();
    const once = Array.from({ length: 11 }, (_, i) => archivo(`c${i}.png`, "image/png"));

    fireEvent.drop(id("adjuntos.zona"), { dataTransfer: { files: once } });

    expect(await screen.findByText("Se pueden adjuntar hasta 10 archivos.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/Subido/)).toHaveLength(10));
    expect(presign).toHaveLength(10);
  });

  it("sin almacenamiento disponible en el plan (402) explica el motivo y no adjunta el archivo", async () => {
    server.use(
      http.post("*/api/v1/tickets/attachments", () =>
        sobreError(402, "ALMACENAMIENTO_AGOTADO", "Límite de almacenamiento."),
      ),
    );
    const tickets = cuerposEnviados("/api/v1/tickets");
    const usuario = abrir();

    await usuario.upload(id("adjuntos.entrada"), archivo("captura.png", "image/png"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Tu organización alcanzó el límite de almacenamiento del plan. · tr_msw_prueba",
    );
    // No es un fallo pasajero: no se ofrece reintentar.
    expect(screen.queryByTestId("nuevo-ticket.adjuntos.reintentar")).not.toBeInTheDocument();

    await completarTicket(usuario);
    await usuario.click(id("formulario.enviar"));
    await waitFor(() => expect(tickets).toHaveLength(1));
    expect(tickets[0]).toMatchObject({ adjuntos: [] });
  });
});

describe("estado de la subida", () => {
  it("mientras sube muestra el progreso y no deja enviar; quitar el archivo cancela la subida", async () => {
    // El doble de MSW no avisa al handler cuando se aborta un XHR: se observa el abort().
    const abortar = vi.spyOn(XMLHttpRequest.prototype, "abort");
    onTestFinished(() => abortar.mockRestore());
    server.use(http.put(`${BLOB}/*`, () => new Promise<never>(() => {})));
    const usuario = abrir();
    await completarTicket(usuario);

    await usuario.upload(id("adjuntos.entrada"), archivo("video.webm", "video/webm"));

    expect(await screen.findByLabelText("Subida de video.webm")).toBeInTheDocument();
    expect(id("formulario.enviar")).toBeDisabled();
    expect(screen.getByText("Espera a que terminen de subir los archivos.")).toBeInTheDocument();

    await usuario.click(screen.getByRole("button", { name: "Quitar video.webm" }));

    expect(filas()).toHaveLength(0);
    expect(id("formulario.enviar")).toBeEnabled();
    expect(abortar).toHaveBeenCalledOnce();
  });

  it("salir del formulario cancela las subidas en curso", async () => {
    const abortar = vi.spyOn(XMLHttpRequest.prototype, "abort");
    onTestFinished(() => abortar.mockRestore());
    let enCurso = 0;
    server.use(
      http.put(`${BLOB}/*`, () => {
        enCurso++;
        return new Promise<never>(() => {});
      }),
    );
    const { unmount } = render(
      <Proveedores>
        <MemoryRouter>
          <NuevoTicket />
        </MemoryRouter>
      </Proveedores>,
    );
    fireEvent.drop(id("adjuntos.zona"), {
      dataTransfer: { files: [archivo("a.png", "image/png"), archivo("b.png", "image/png")] },
    });
    // Las dos ya están subiendo al blob (no solo pidiendo la URL firmada).
    await waitFor(() => expect(enCurso).toBe(2));

    unmount();

    expect(abortar).toHaveBeenCalledTimes(2);
  });

  it("si el almacenamiento rechaza la subida se puede reintentar", async () => {
    let intentos = 0;
    server.use(
      http.put(`${BLOB}/*`, () => new HttpResponse(null, { status: ++intentos === 1 ? 403 : 201 })),
    );
    const usuario = abrir();

    await usuario.upload(id("adjuntos.entrada"), archivo("captura.png", "image/png"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo subir el archivo. Inténtalo de nuevo.",
    );
    await usuario.click(id("adjuntos.reintentar"));

    expect(await screen.findByText(/Subido/)).toBeInTheDocument();
    expect(intentos).toBe(2);
  });

  it("una upload_url que no es http(s) se rechaza sin subir nada", async () => {
    const consola = vi.spyOn(console, "error").mockImplementation(() => {});
    onTestFinished(() => consola.mockRestore());
    let subidas = 0;
    server.use(
      http.post("*/api/v1/tickets/attachments", () =>
        HttpResponse.json(
          { adjunto_id: "att_1", upload_url: "javascript:alert(1)", expira_en: "x" },
          { status: 201 },
        ),
      ),
      http.put("*", () => {
        subidas++;
        return new HttpResponse(null, { status: 201 });
      }),
    );
    const usuario = abrir();

    await usuario.upload(id("adjuntos.entrada"), archivo("captura.png", "image/png"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/formato esperado/);
    expect(subidas).toBe(0);
  });
});
