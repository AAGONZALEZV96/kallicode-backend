/**
 * Subida y validación de adjuntos.
 *
 * Flujo:
 * presign → PUT a Blob → confirmación → consulta de estado → listo.
 */
import { z } from "zod";

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

const INTERVALO_ESCANEO_MS = 1_000;
const MAX_CONSULTAS_ESCANEO = 120;

const AdjuntoEstadoEsquema = z.object({
  adjunto_id: z.string(),
  estado: z.enum([
    "reservado",
    "pendiente_escaneo",
    "listo",
    "rechazado",
  ]),
  escaneo: z.enum(["pendiente", "limpio", "infectado"]),
  tamano_bytes: z.number().int().nonnegative(),
  content_type: z.string(),
  motivo: z.string().nullable().optional(),
});

type EstadoAdjunto = z.infer<typeof AdjuntoEstadoEsquema>;

interface OpcionesSubida {
  alProgreso: (fraccion: number) => void;
  signal: AbortSignal;
}

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
  if (!tipoAdjunto(archivo)) return "TIPO_NO_PERMITIDO";
  if (archivo.size === 0) return "ARCHIVO_VACIO";
  if (archivo.size > MAX_ADJUNTO_BYTES) return "ADJUNTO_DEMASIADO_GRANDE";
  return null;
}

function errorCancelacion(): DOMException {
  return new DOMException("La subida se canceló.", "AbortError");
}

/** Espera cancelable que retira sus listeners al terminar. */
function esperar(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(errorCancelacion());
      return;
    }

    const cancelar = () => {
      window.clearTimeout(temporizador);
      signal.removeEventListener("abort", cancelar);
      reject(errorCancelacion());
    };

    const temporizador = window.setTimeout(() => {
      signal.removeEventListener("abort", cancelar);
      resolve();
    }, ms);

    signal.addEventListener("abort", cancelar, { once: true });
  });
}

function comprobarResultado(estado: EstadoAdjunto): boolean {
  if (estado.escaneo === "infectado" || estado.estado === "rechazado") {
    throw new ApiError(423, {
      codigo: "ADJUNTO_BLOQUEADO",
      mensaje:
        estado.escaneo === "infectado"
          ? "El archivo fue bloqueado por el análisis de seguridad."
          : "El archivo fue rechazado durante su validación.",
    });
  }

  return estado.estado === "listo" && estado.escaneo === "limpio";
}

async function esperarEscaneo(
  adjuntoId: string,
  signal: AbortSignal,
): Promise<void> {
  const ruta = `/api/v1/tickets/attachments/${encodeURIComponent(adjuntoId)}/status`;

  for (let intento = 0; intento < MAX_CONSULTAS_ESCANEO; intento += 1) {
    if (signal.aborted) {
      throw errorCancelacion();
    }

    const estado = await api.get(ruta, {
      esquema: AdjuntoEstadoEsquema,
      signal,
    });

    if (comprobarResultado(estado)) {
      return;
    }

    if (intento < MAX_CONSULTAS_ESCANEO - 1) {
      await esperar(INTERVALO_ESCANEO_MS, signal);
    }
  }

  throw new ApiError(504, {
    codigo: "ESCANEO_AGOTADO",
    mensaje:
      "El análisis del archivo está tardando más de lo esperado. " +
      "Todavía no se puede adjuntar al ticket.",
  });
}

/** Devuelve el ID únicamente después de confirmar y analizar el archivo. */
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

  const { adjunto_id, upload_url } = await api.post(
    "/api/v1/tickets/attachments",
    cuerpo,
    {
      esquema: AdjuntoPresignEsquema,
      signal,
    },
  );

  await enviarAlBlob(
    upload_url,
    archivo,
    tipo,
    alProgreso,
    signal,
  );

  const confirmacion = await api.post(
    `/api/v1/tickets/attachments/${encodeURIComponent(adjunto_id)}/confirm`,
    {},
    {
      esquema: AdjuntoEstadoEsquema,
      signal,
    },
  );

  if (!comprobarResultado(confirmacion)) {
    await esperarEscaneo(adjunto_id, signal);
  }

  return adjunto_id;
}

/** PUT directo al almacenamiento, sin enviar el Bearer de la sesión. */
function enviarAlBlob(
  url: string,
  archivo: File,
  tipo: string,
  alProgreso: (fraccion: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolver, rechazar) => {
    if (signal.aborted) {
      rechazar(errorCancelacion());
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
    xhr.timeout = 120_000;
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

    xhr.ontimeout = () => {
      terminar();
      rechazar(fallo(408));
    };

    xhr.onabort = () => {
      terminar();
      rechazar(errorCancelacion());
    };

    signal.addEventListener("abort", abortar, { once: true });
    xhr.send(archivo);
  });
}