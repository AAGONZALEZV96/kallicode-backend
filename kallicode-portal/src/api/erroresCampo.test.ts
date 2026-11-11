/** Errores por campo (KC-PT-06-5): traducción de Pydantic, reparto del 422 y reglas del portal. */
import { describe, expect, it } from "vitest";

import { DICT } from "../estado/i18n";
import { ApiError } from "./client";
import { errorDeTexto, erroresDelServidor, mensajeDeCampo } from "./erroresCampo";

const t = (clave: string, valores?: Record<string, string>) =>
  (DICT.es[clave] ?? clave).replace(/\{(\w+)\}/g, (_, n: string) => valores?.[n] ?? "");

const un422 = (campos: { campo: string; error: string }[] | undefined) =>
  new ApiError(422, {
    codigo: "VALIDACION_ENTRADA",
    mensaje: "Indica el motivo de la cancelación.",
    ...(campos ? { detalle: { campos } } : {}),
  });

describe("mensajeDeCampo", () => {
  it.each([
    ["Field required", "Este campo es obligatorio."],
    ["String should have at least 5 characters", "Debe tener al menos 5 caracteres."],
    ["String should have at most 200 characters", "Puede tener como máximo 200 caracteres."],
    [
      "List should have at most 10 items after validation, not 11",
      "Puede tener como máximo 10 elementos.",
    ],
    ["String should match pattern '^(bug|mejora)$'", "El formato no es válido."],
    ["Un mensaje que el portal no conoce", "Un mensaje que el portal no conoce"],
  ])("%s → %s", (mensaje, esperado) => {
    expect(mensajeDeCampo(t, mensaje)).toBe(esperado);
  });
});

describe("erroresDelServidor", () => {
  const aCampo = (campo: string) => (["titulo", "descripcion"].includes(campo) ? campo : null);

  it("pone cada error en su campo, traducido, y deja aparte los que el formulario no tiene", () => {
    const error = un422([
      { campo: "titulo", error: "String should have at least 5 characters" },
      { campo: "adjuntos", error: "List should have at most 10 items after validation, not 11" },
    ]);

    expect(erroresDelServidor(t, error, aCampo)).toEqual({
      porCampo: { titulo: "Debe tener al menos 5 caracteres." },
      sueltos: ["adjuntos: Puede tener como máximo 10 elementos."],
    });
  });

  it("si un campo trae varios errores muestra el primero", () => {
    const error = un422([
      { campo: "titulo", error: "Field required" },
      { campo: "titulo", error: "String should have at least 5 characters" },
    ]);

    expect(erroresDelServidor(t, error, aCampo).porCampo).toEqual({
      titulo: "Este campo es obligatorio.",
    });
  });

  it("un 422 sin campos va al campo indicado, con el mensaje del backend", () => {
    expect(erroresDelServidor(t, un422(undefined), aCampo, "motivo").porCampo).toEqual({
      motivo: "Indica el motivo de la cancelación.",
    });
    expect(erroresDelServidor(t, un422(undefined), aCampo).porCampo).toEqual({});
  });

  it("los demás errores no son de campos", () => {
    const conflicto = new ApiError(409, { codigo: "CANCELACION_TARDIA", mensaje: "Tarde." });

    expect(erroresDelServidor(t, conflicto, aCampo)).toEqual({ porCampo: {}, sueltos: [] });
    expect(erroresDelServidor(t, null, aCampo)).toEqual({ porCampo: {}, sueltos: [] });
  });
});

describe("errorDeTexto (las reglas Field del backend)", () => {
  it.each([
    ["", { min: 5 }, "Este campo es obligatorio."],
    ["    ", { min: 5 }, "Este campo es obligatorio."],
    ["abcd", { min: 5 }, "Debe tener al menos 5 caracteres."],
    // Los espacios de los extremos no cuentan para el mínimo.
    ["  abcd  ", { min: 5 }, "Debe tener al menos 5 caracteres."],
    ["abcde", { min: 5 }, undefined],
    ["x".repeat(201), { min: 5, max: 200 }, "Puede tener como máximo 200 caracteres."],
    ["", { max: 200 }, undefined],
  ])("%j con %j → %s", (valor, regla, esperado) => {
    expect(errorDeTexto(t, valor, regla)).toBe(esperado);
  });
});
