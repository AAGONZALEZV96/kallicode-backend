/**
 * Adjuntos: reserva, subida directa al blob y confirmación del backend.
 *
 * El identificador solo se devuelve cuando el archivo está confirmado
 * y completó el análisis de seguridad.
 */
import { api, ApiError } from "./client";
import { AdjuntoPresignEsquema, type AdjuntoIn } from "./tipos";

export const TIPOS_ADJUNTO = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "video/mp4",
  "video/webm",
  "application/pdf",
  "text/markdown",
  "text/plain",
  "text/csv",
  "application/zip",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
];

const TIPO_POR_EXTENSION: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  mp4: "video/mp4",
  webm: "video/webm",
  pdf: "application/pdf",
  md: "text/markdown",
  markdown: "text/markdown",
  txt: "text/plain",
  csv: "text/csv",
  zip: "application/zip",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export const ACEPTA_ADJUNTOS = [
  ...TIPOS_ADJUNTO,
  ...Object.keys(TIPO_POR_EXTENSION).map((extension) => `.${extension}`),
].join(",");

export const MAX_ADJUNTO_BYTES = 26_214_400;
export const MAX_ADJUNTOS = 10;

export function tipoAdjunto(archivo: File): string | null {
  if (TIPOS_ADJUNTO.includes(archivo.type)) {
    return archivo.type;
  }

  const extension = archivo.name.split(".").pop()?.toLowerCase() ?? "";

  return TIPO_POR_EXTENSION[extension] ?? null;
}

export function revisarAdjunto(
  archivo: File,
): "TIPO_NO_PERMITIDO" | "ARCHIVO_VACIO" | "ADJUNTO_DEMASIADO_GRANDE" | null {
  if (!tipoAdjunto(archivo)) {
    return "TIPO_NO_PERMITIDO";
  }

  if (archivo.size === 0) {
    return "ARCHIVO_VACIO";
  }

  if (archivo.size > MAX_ADJUNTO_BYTES) {
    return "ADJUNTO_DEMASIADO_GRANDE";
  }

  return null;
}

interface OpcionesSubida {
  alProgreso: (fraccion: number) => void;
  signal: AbortSignal;
}

export async function subirAdjunto(
  archivo: File,
  tipo: string,
  { alProgreso, signal }: OpcionesSubida,
): Promise<string> {
  const cuerpo: AdjuntoIn = {
    nombre_archivo: archivo.name,
    content_type: tipo,
    tamano_bytes: archivo.size,
  };

  const { adjunto_id, upload_url } = await api.post("/api/v1/tickets/attachments", cuerpo, {
    esquema: AdjuntoPresignEsquema,
    signal,
  });

  await enviarAlBlob(upload_url, archivo, tipo, alProgreso, signal);

  await api.post(`/api/v1/tickets/attachments/${adjunto_id}/confirm`, {}, { signal });

  return adjunto_id;
}

function enviarAlBlob(
  url: string,
  archivo: File,
  tipo: string,
  alProgreso: (fraccion: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolver, rechazar) => {
    const cancelada = () => new DOMException("La subida se canceló.", "AbortError");

    if (signal.aborted) {
      rechazar(cancelada());
      return;
    }

    const xhr = new XMLHttpRequest();

    const abortar = () => xhr.abort();

    const terminar = () => signal.removeEventListener("abort", abortar);

    const fallo = (http: number) =>
      new ApiError(http, {
        codigo: "SUBIDA_FALLIDA",
        mensaje: "No se pudo subir el archivo al almacenamiento.",
      });

    xhr.open("PUT", url);
    xhr.setRequestHeader("x-ms-blob-type", "BlockBlob");
    xhr.setRequestHeader("Content-Type", tipo);

    xhr.upload.onprogress = (evento) => {
      if (evento.lengthComputable && evento.total > 0) {
        alProgreso(evento.loaded / evento.total);
      }
    };

    xhr.onload = () => {
      terminar();

      if (xhr.status >= 200 && xhr.status < 300) {
        resolver();
      } else {
        rechazar(fallo(xhr.status));
      }
    };

    xhr.onerror = () => {
      terminar();
      rechazar(fallo(0));
    };

    xhr.onabort = () => {
      terminar();
      rechazar(cancelada());
    };

    signal.addEventListener("abort", abortar, { once: true });

    xhr.send(archivo);
  });
}
