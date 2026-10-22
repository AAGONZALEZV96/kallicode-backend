/**
 * Adjuntos al crear un ticket (KC-PT-05-1).
 *
 * El archivo nunca pasa por la API: POST /tickets/attachments revisa tipo, tamaño y cuota
 * de almacenamiento del plan y devuelve una URL firmada; el navegador sube el archivo
 * directo al blob con PUT y el ticket se crea con los `adjunto_id`.
 *
 * El portal aplica antes las mismas reglas que el backend para no pedir URLs que se van a
 * rechazar; el backend sigue siendo quien decide (y el único que conoce la cuota).
 */
import { api, ApiError } from "./client";
import { AdjuntoPresignEsquema, type AdjuntoIn } from "./tipos";

/** Formatos aceptados (_TIPOS_ADJUNTO en apps/api/.../routers/tickets.py). */
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

/**
 * Tipo por extensión, para cuando el navegador no informa uno aceptado: Windows suele
 * dejar vacío el de .md y entrega .csv y .zip con tipos propios de Office o de Windows.
 */
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

/** `accept` del selector de archivos: tipos y extensiones. */
export const ACEPTA_ADJUNTOS = [
  ...TIPOS_ADJUNTO,
  ...Object.keys(TIPO_POR_EXTENSION).map((e) => `.${e}`),
].join(",");

/** max_adjunto_bytes del backend (25 MB). */
export const MAX_ADJUNTO_BYTES = 26_214_400;
/** maxItems de `adjuntos` en TicketIn y FeatureIn. */
export const MAX_ADJUNTOS = 10;

/** Tipo con el que se declara el archivo al backend, o null si no es un formato aceptado. */
export function tipoAdjunto(archivo: File): string | null {
  if (TIPOS_ADJUNTO.includes(archivo.type)) return archivo.type;
  const extension = archivo.name.split(".").pop()?.toLowerCase() ?? "";
  return TIPO_POR_EXTENSION[extension] ?? null;
}

/** El mismo código que respondería el backend, o null si el archivo se puede subir. */
export function revisarAdjunto(
  archivo: File,
): "TIPO_NO_PERMITIDO" | "ARCHIVO_VACIO" | "ADJUNTO_DEMASIADO_GRANDE" | null {
  if (!tipoAdjunto(archivo)) return "TIPO_NO_PERMITIDO";
  if (archivo.size === 0) return "ARCHIVO_VACIO";
  if (archivo.size > MAX_ADJUNTO_BYTES) return "ADJUNTO_DEMASIADO_GRANDE";
  return null;
}

interface OpcionesSubida {
  /** Avance del envío al blob, de 0 a 1. */
  alProgreso: (fraccion: number) => void;
  signal: AbortSignal;
}

/** Pide la URL firmada y sube el archivo. Devuelve el `adjunto_id` para crear el ticket. */
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
  return adjunto_id;
}

/**
 * PUT directo al blob con XMLHttpRequest: fetch no informa el progreso del envío. No lleva
 * el Bearer de la sesión; la credencial es la firma de la URL.
 */
function enviarAlBlob(
  url: string,
  archivo: File,
  tipo: string,
  alProgreso: (fraccion: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolver, rechazar) => {
    const cancelada = () => new DOMException("La subida se canceló.", "AbortError");
    if (signal.aborted) return rechazar(cancelada());
    const xhr = new XMLHttpRequest();
    const abortar = () => xhr.abort();
    const terminar = () => signal.removeEventListener("abort", abortar);
    const fallo = (http: number) =>
      new ApiError(http, {
        codigo: "SUBIDA_FALLIDA",
        mensaje: "No se pudo subir el archivo al almacenamiento.",
      });

    xhr.open("PUT", url);
    // Azure Blob exige el tipo de blob en un PUT directo.
    xhr.setRequestHeader("x-ms-blob-type", "BlockBlob");
    xhr.setRequestHeader("Content-Type", tipo);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) alProgreso(e.loaded / e.total);
    };
    xhr.onload = () => {
      terminar();
      if (xhr.status >= 200 && xhr.status < 300) resolver();
      else rechazar(fallo(xhr.status));
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
