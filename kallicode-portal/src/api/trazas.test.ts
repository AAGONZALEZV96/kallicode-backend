import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorTraza, formatearTraza, leerTraza, MAX_CARACTERES_TRAZA } from "./trazas";

afterEach(() => vi.restoreAllMocks());

describe("formatearTraza", () => {
  it("da sangría al JSON", () => {
    expect(formatearTraza('{"a":1,"b":[2]}')).toEqual({
      texto: '{\n  "a": 1,\n  "b": [\n    2\n  ]\n}',
      esJson: true,
      truncada: false,
    });
  });

  it("deja el texto que no es JSON tal cual", () => {
    expect(formatearTraza("<b>hola</b>")).toEqual({
      texto: "<b>hola</b>",
      esJson: false,
      truncada: false,
    });
  });

  it("corta lo que pasa del máximo y lo avisa", () => {
    const traza = formatearTraza("x".repeat(MAX_CARACTERES_TRAZA + 10));
    expect(traza.texto).toHaveLength(MAX_CARACTERES_TRAZA);
    expect(traza.truncada).toBe(true);
  });
});

describe("leerTraza", () => {
  it("pide la URL firmada sin credenciales ni Referer", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"a":1}'));

    await leerTraza("https://almacen.test/t/entrada.json?sig=1");

    expect(fetch).toHaveBeenCalledWith("https://almacen.test/t/entrada.json?sig=1", {
      signal: undefined,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
  });

  it("no pide una URL que no sea http(s)", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");

    await expect(leerTraza("javascript:alert(1)")).rejects.toEqual(new ErrorTraza(null));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("un error de red (CORS) queda sin estado; uno HTTP, con su estado", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(leerTraza("https://almacen.test/x")).rejects.toMatchObject({ estado: null });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status: 403 }));
    await expect(leerTraza("https://almacen.test/x")).rejects.toMatchObject({ estado: 403 });
  });
});
