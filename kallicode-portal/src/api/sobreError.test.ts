/** Cuerpo de un error: el sobre del backend o un Problem Details de RFC 7807 (KC-PT-06-5). */
import { describe, expect, it } from "vitest";

import { leerSobreError } from "./sobreError";

describe("leerSobreError", () => {
  it("lee el sobre del backend, con los campos de un 422", () => {
    const cuerpo = {
      error: {
        codigo: "VALIDACION_ENTRADA",
        mensaje: "Revisa los campos marcados.",
        detalle: {
          campos: [{ campo: "titulo", error: "String should have at least 5 characters" }],
        },
        trace_id: "tr_1",
        timestamp: "2026-11-11T12:00:00Z",
      },
    };

    expect(leerSobreError(cuerpo, 422)).toEqual({
      codigo: "VALIDACION_ENTRADA",
      mensaje: "Revisa los campos marcados.",
      detalle: { campos: [{ campo: "titulo", error: "String should have at least 5 characters" }] },
      trace_id: "tr_1",
    });
  });

  it("un sobre sin mensaje ni detalle sigue siendo un error con su código", () => {
    expect(leerSobreError({ error: { codigo: "NO_AUTENTICADO" } }, 401)).toEqual({
      codigo: "NO_AUTENTICADO",
      mensaje: "Error 401",
    });
  });

  it("lee un Problem Details con `errors` (nombre o JSON Pointer)", () => {
    const problema = {
      type: "https://kallicode.dev/problemas/validacion",
      title: "Datos inválidos",
      status: 422,
      detail: "Revisa los campos marcados.",
      errors: [
        { name: "titulo", reason: "String should have at least 5 characters" },
        { pointer: "/criterios/0/given", detail: "Field required" },
        { reason: "sin campo: se descarta" },
      ],
    };

    expect(leerSobreError(problema, 422)).toEqual({
      codigo: "VALIDACION_ENTRADA",
      mensaje: "Revisa los campos marcados.",
      detalle: {
        campos: [
          { campo: "titulo", error: "String should have at least 5 characters" },
          { campo: "criterios.0.given", error: "Field required" },
        ],
      },
    });
  });

  it("lee `invalid-params` (el ejemplo de la RFC 7807) y un código propio si lo trae", () => {
    const problema = {
      title: "Your request parameters didn't validate.",
      code: "PARAMETROS_INVALIDOS",
      "invalid-params": [{ name: "age", reason: "must be a positive integer" }],
    };

    expect(leerSobreError(problema, 400)).toEqual({
      codigo: "PARAMETROS_INVALIDOS",
      mensaje: "Your request parameters didn't validate.",
      detalle: { campos: [{ campo: "age", error: "must be a positive integer" }] },
    });
  });

  it("un Problem Details sin código ni errores usa el estado HTTP", () => {
    expect(leerSobreError({ title: "Conflicto", status: 409 }, 409)).toEqual({
      codigo: "HTTP_409",
      mensaje: "Conflicto",
    });
  });

  it.each([null, "texto", { otra: "cosa" }])(
    "un cuerpo desconocido (%j) da ERROR_DESCONOCIDO",
    (cuerpo) => {
      expect(leerSobreError(cuerpo, 500)).toEqual({
        codigo: "ERROR_DESCONOCIDO",
        mensaje: "Error 500",
      });
    },
  );
});
