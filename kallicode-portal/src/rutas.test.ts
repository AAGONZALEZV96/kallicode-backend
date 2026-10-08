import { describe, expect, it } from "vitest";

import { destinoSeguro, leerAvisoAcceso } from "./rutas";

describe("destinoSeguro", () => {
  it.each([
    ["/tickets/KC-1001", "/tickets/KC-1001"],
    ["/auditoria?page=2#arriba", "/auditoria?page=2#arriba"],
    ["/tickets/../aprobaciones", "/aprobaciones"],
  ])("conserva la ruta interna %s", (siguiente, esperado) => {
    expect(destinoSeguro(siguiente)).toBe(esperado);
  });

  it.each([
    [null],
    [""],
    ["tickets"],
    ["https://malo.example/robar"],
    ["javascript:alert(1)"],
    ["//malo.example/robar"],
    ["/\\malo.example/robar"],
    ["/a/..//malo.example/robar"],
  ])("descarta %s y lleva al inicio", (siguiente) => {
    expect(destinoSeguro(siguiente)).toBe("/");
  });
});

describe("leerAvisoAcceso", () => {
  it("lee un aviso válido", () => {
    expect(leerAvisoAcceso({ rutaDenegada: "/conexiones", rol: "viewer" })).toEqual({
      rutaDenegada: "/conexiones",
      rol: "viewer",
    });
  });

  it.each([
    null,
    undefined,
    "texto",
    {},
    { rutaDenegada: "/conexiones" },
    { rutaDenegada: 3, rol: "viewer" },
    { rutaDenegada: "/conexiones", rol: "arquitecto" },
    { errorAcceso: "Tu rol (viewer) no permite acceder a /conexiones" },
  ])("ignora %j", (estado) => {
    expect(leerAvisoAcceso(estado)).toBeNull();
  });
});
