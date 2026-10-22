/** Reglas de adjuntos que el portal aplica antes de pedir la URL firmada (KC-PT-05-1). */
import { describe, expect, it } from "vitest";

import { MAX_ADJUNTO_BYTES, revisarAdjunto, tipoAdjunto } from "./adjuntos";

const archivo = (nombre: string, tipo: string, bytes = 10) =>
  new File([new Uint8Array(bytes)], nombre, { type: tipo });

describe("tipoAdjunto", () => {
  it.each([
    ["captura.png", "image/png", "image/png"],
    ["informe.pdf", "application/pdf", "application/pdf"],
    // Windows deja vacío el tipo de .md y entrega .csv y .zip con tipos propios.
    ["notas.md", "", "text/markdown"],
    ["datos.csv", "application/vnd.ms-excel", "text/csv"],
    ["logs.zip", "application/x-zip-compressed", "application/zip"],
    ["Spec.DOCX", "", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  ])("%s (%s) se declara como %s", (nombre, tipo, esperado) => {
    expect(tipoAdjunto(archivo(nombre, tipo))).toBe(esperado);
  });

  it.each([
    ["script.exe", "application/x-msdownload"],
    ["pagina.html", "text/html"],
    ["sin-extension", ""],
  ])("%s no es un formato aceptado", (nombre, tipo) => {
    expect(tipoAdjunto(archivo(nombre, tipo))).toBeNull();
  });
});

describe("revisarAdjunto", () => {
  const conTamano = (bytes: number) => {
    const f = archivo("video.mp4", "video/mp4");
    Object.defineProperty(f, "size", { value: bytes });
    return f;
  };

  it("acepta hasta 25 MB exactos", () => {
    expect(revisarAdjunto(conTamano(MAX_ADJUNTO_BYTES))).toBeNull();
  });

  it.each([
    [archivo("a.exe", "application/x-msdownload"), "TIPO_NO_PERMITIDO"],
    [archivo("vacio.png", "image/png", 0), "ARCHIVO_VACIO"],
    [conTamano(MAX_ADJUNTO_BYTES + 1), "ADJUNTO_DEMASIADO_GRANDE"],
  ])("%s → %s", (f, codigo) => {
    expect(revisarAdjunto(f)).toBe(codigo);
  });
});
