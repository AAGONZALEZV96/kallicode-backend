import { describe, expect, it } from "vitest";

import { abreviarSello, estadoSello } from "./integridad";

const verificacion = (integra: boolean, primer_evento_roto: number | null) => ({
  integra,
  alcance: "tramo_ticket" as const,
  tramo_integro: integra,
  eventos_verificados: 5,
  primer_evento_roto,
});

describe("estadoSello", () => {
  it("con el tramo íntegro, todos los eventos quedan confirmados", () => {
    expect([1, 2, 3].map((id) => estadoSello(id, verificacion(true, null)))).toEqual([
      "confirmado",
      "confirmado",
      "confirmado",
    ]);
  });

  it("con un sello alterado: antes confirmado, ese alterado y después sin confirmar", () => {
    expect([9, 10, 11].map((id) => estadoSello(id, verificacion(false, 10)))).toEqual([
      "confirmado",
      "alterado",
      "sin-confirmar",
    ]);
  });

  it("roto sin primer evento informado (cadena del tenant): no marca eventos", () => {
    expect(estadoSello(1, verificacion(false, null))).toBe("confirmado");
  });
});

describe("abreviarSello", () => {
  it("deja 8 caracteres al inicio y 4 al final", () => {
    expect(abreviarSello("a".repeat(8) + "b".repeat(52) + "c".repeat(4))).toBe("aaaaaaaa…cccc");
  });

  it("un sello corto queda entero", () => {
    expect(abreviarSello("a1b2c3")).toBe("a1b2c3");
  });
});
