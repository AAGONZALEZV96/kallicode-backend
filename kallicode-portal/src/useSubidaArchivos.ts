/**
 * Estado de los adjuntos de un formulario (KC-PT-05-1): cada archivo se sube apenas se
 * agrega y el formulario solo envía los `adjunto_id` de los que terminaron.
 */
import { useEffect, useRef, useState, type ClipboardEvent } from "react";

import { MAX_ADJUNTOS, revisarAdjunto, subirAdjunto, tipoAdjunto } from "./api/adjuntos";
import { ApiError } from "./api/client";

export interface Adjunto {
  clave: string;
  archivo: File;
  estado: "subiendo" | "lista" | "error";
  /** Avance de la subida, de 0 a 1. */
  progreso: number;
  adjuntoId?: string;
  error?: ApiError;
}

/** Fallos de red o del almacenamiento: subir de nuevo puede funcionar. */
const REINTENTABLES = ["SUBIDA_FALLIDA", "SIN_CONEXION", "TIEMPO_AGOTADO"];
export const sePuedeReintentar = (a: Adjunto) =>
  a.estado === "error" && REINTENTABLES.includes(a.error?.codigo ?? "");

const MENSAJES_LOCALES: Record<string, string> = {
  TIPO_NO_PERMITIDO: "Tipo de archivo no permitido.",
  ARCHIVO_VACIO: "El archivo está vacío.",
  ADJUNTO_DEMASIADO_GRANDE: "El archivo supera el máximo de 25 MB.",
  DEMASIADOS_ADJUNTOS: `Se pueden adjuntar hasta ${MAX_ADJUNTOS} archivos.`,
};
const errorLocal = (codigo: string) =>
  new ApiError(0, { codigo, mensaje: MENSAJES_LOCALES[codigo] ?? codigo });

export function useSubidaArchivos() {
  const [adjuntos, setAdjuntos] = useState<Adjunto[]>([]);
  const controles = useRef(new Map<string, AbortController>());

  // Salir del formulario cancela las subidas en curso.
  useEffect(() => {
    const enCurso = controles.current;
    return () => enCurso.forEach((c) => c.abort());
  }, []);

  const actualizar = (clave: string, cambios: Partial<Adjunto>) =>
    setAdjuntos((previos) => previos.map((a) => (a.clave === clave ? { ...a, ...cambios } : a)));

  function subir(clave: string, archivo: File) {
    const control = new AbortController();
    controles.current.set(clave, control);
    subirAdjunto(archivo, tipoAdjunto(archivo) ?? archivo.type, {
      signal: control.signal,
      alProgreso: (progreso) => actualizar(clave, { progreso }),
    })
      .then((adjuntoId) => actualizar(clave, { estado: "lista", progreso: 1, adjuntoId }))
      .catch((e: unknown) => {
        if (control.signal.aborted) return;
        const error =
          e instanceof ApiError
            ? e
            : new ApiError(0, { codigo: "SUBIDA_FALLIDA", mensaje: String(e) });
        actualizar(clave, { estado: "error", error });
      })
      .finally(() => controles.current.delete(clave));
  }

  function agregar(archivos: FileList | File[]) {
    // Los rechazados no ocupan cupo: se muestran para explicar por qué no se suben.
    let cupo = MAX_ADJUNTOS - adjuntos.filter((a) => a.estado !== "error").length;
    const nuevos = [...archivos].map((archivo): Adjunto => {
      const codigo = revisarAdjunto(archivo) ?? (cupo > 0 ? null : "DEMASIADOS_ADJUNTOS");
      if (!codigo) cupo--;
      const clave = crypto.randomUUID();
      return codigo
        ? { clave, archivo, estado: "error", progreso: 0, error: errorLocal(codigo) }
        : { clave, archivo, estado: "subiendo", progreso: 0 };
    });
    setAdjuntos((previos) => [...previos, ...nuevos]);
    for (const a of nuevos) if (a.estado === "subiendo") subir(a.clave, a.archivo);
  }

  function quitar(clave: string) {
    controles.current.get(clave)?.abort();
    setAdjuntos((previos) => previos.filter((a) => a.clave !== clave));
  }

  function reintentar(clave: string) {
    const adjunto = adjuntos.find((a) => a.clave === clave);
    if (!adjunto || !sePuedeReintentar(adjunto)) return;
    actualizar(clave, { estado: "subiendo", progreso: 0, error: undefined });
    subir(clave, adjunto.archivo);
  }

  /** Pegar una captura en el formulario la adjunta; el texto se pega como siempre. */
  function alPegar(e: ClipboardEvent) {
    if (e.clipboardData.files.length === 0) return;
    e.preventDefault();
    agregar(e.clipboardData.files);
  }

  return {
    adjuntos,
    agregar,
    quitar,
    reintentar,
    alPegar,
    /** `adjunto_id` de los archivos ya subidos, para crear el ticket. */
    ids: adjuntos.flatMap((a) => (a.estado === "lista" && a.adjuntoId ? [a.adjuntoId] : [])),
    subiendo: adjuntos.some((a) => a.estado === "subiendo"),
  };
}

export type SubidaArchivos = ReturnType<typeof useSubidaArchivos>;
