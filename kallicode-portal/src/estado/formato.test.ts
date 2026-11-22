import { describe, expect, it } from "vitest";

import { crearFormato, zonaValida } from "./formato";

/** Intl separa con espacios no separables (U+00A0, U+202F): se comparan como espacios. */
const n = (texto: string) => texto.replace(/\s/g, " ");

// 2026-11-21 17:30:05 UTC: 14:30 en Santiago (UTC-3), 18:30 en Madrid (UTC+1).
const ISO = "2026-11-21T17:30:05Z";

describe("fechas en la zona de la organización y el idioma del portal", () => {
  it("la misma fecha UTC se muestra en la hora de la organización", () => {
    expect(crearFormato("es", "America/Santiago").hora(ISO)).toBe("14:30");
    expect(crearFormato("es", "Europe/Madrid").hora(ISO)).toBe("18:30");
    expect(crearFormato("es", "Europe/Madrid").horaConSegundos(ISO)).toBe("18:30:05");
  });

  it("el formato sigue al idioma del portal, no al del navegador", () => {
    expect(n(crearFormato("es", "UTC").fecha(ISO))).toBe("21/11, 17:30");
    expect(n(crearFormato("en", "UTC").fecha(ISO))).toBe("11/21, 05:30 PM");
    expect(n(crearFormato("pt", "UTC").fecha(ISO))).toBe("21/11, 17:30");
  });

  it("la fecha larga lleva el año", () => {
    expect(n(crearFormato("es", "UTC").fechaLarga(ISO))).toBe("21 nov 2026, 17:30");
    expect(n(crearFormato("en", "UTC").fechaLarga(ISO))).toBe("Nov 21, 2026, 5:30 PM");
  });

  it("la fecha cruza de día según la zona", () => {
    const tarde = "2026-11-21T23:30:00Z";
    expect(n(crearFormato("es", "Europe/Madrid").fecha(tarde))).toBe("22/11, 00:30");
    expect(n(crearFormato("es", "America/Santiago").fecha(tarde))).toBe("21/11, 20:30");
  });

  it("sin fecha, o con una inválida, muestra «—»", () => {
    const f = crearFormato("es", "UTC");
    expect([f.fecha(null), f.fecha(undefined), f.fecha(""), f.fecha("no-es-fecha")]).toEqual([
      "—",
      "—",
      "—",
      "—",
    ]);
    expect(f.hora(null)).toBe("—");
    expect(f.relativo(null)).toBe("—");
  });

  it("una zona que no existe no rompe: usa la del navegador", () => {
    expect(zonaValida("Marte/Olympus")).toBeUndefined();
    expect(zonaValida("Europe/Madrid")).toBe("Europe/Madrid");
    expect(zonaValida("")).toBeUndefined();
    const f = crearFormato("es", "Marte/Olympus");
    expect(f.zona).toBeUndefined();
    expect(f.fecha(ISO)).not.toBe("—");
  });
});

describe("tiempos relativos", () => {
  const ahora = Date.parse(ISO);
  const antes = (segundos: number) => new Date(ahora - segundos * 1000).toISOString();

  it.each([
    [10, "hace 10 segundos"],
    [5 * 60, "hace 5 minutos"],
    [3 * 3600, "hace 3 horas"],
    [24 * 3600, "ayer"],
    [3 * 24 * 3600, "hace 3 días"],
    [60 * 24 * 3600, "hace 2 meses"],
    [400 * 24 * 3600, "el año pasado"],
  ])("hace %i s en español: %s", (segundos, esperado) => {
    expect(crearFormato("es", "UTC").relativo(antes(segundos), ahora)).toBe(esperado);
  });

  it("también hacia adelante y en otros idiomas", () => {
    expect(crearFormato("es", "UTC").relativo(antes(-2 * 3600), ahora)).toBe("dentro de 2 horas");
    expect(crearFormato("en", "UTC").relativo(antes(5 * 60), ahora)).toBe("5 minutes ago");
    expect(crearFormato("pt", "UTC").relativo(antes(5 * 60), ahora)).toBe("há 5 minutos");
  });
});

describe("números", () => {
  it("separadores del idioma del portal", () => {
    expect(crearFormato("es", "UTC").numero(1520)).toBe("1520");
    expect(crearFormato("es", "UTC").numero(15200)).toBe("15.200");
    expect(crearFormato("en", "UTC").numero(15200)).toBe("15,200");
    expect(crearFormato("pt", "UTC").numero(15200)).toBe("15.200");
    expect(crearFormato("es", "UTC").numero(Number.NaN)).toBe("—");
  });

  it("compactos", () => {
    expect(n(crearFormato("es", "UTC").compacto(1838))).toBe("1,8 mil");
    expect(crearFormato("en", "UTC").compacto(1838)).toBe("1.8K");
    expect(crearFormato("en", "UTC").compacto(320)).toBe("320");
  });

  it("porcentajes a partir de 0–100", () => {
    expect(n(crearFormato("es", "UTC").porcentaje(45))).toBe("45 %");
    expect(crearFormato("en", "UTC").porcentaje(45)).toBe("45%");
    expect(crearFormato("en", "UTC").porcentaje(87.456, 1)).toBe("87.5%");
    expect(crearFormato("en", "UTC").porcentaje(null)).toBe("—");
  });

  it("montos con su moneda", () => {
    expect(n(crearFormato("es", "UTC").moneda(49, "USD"))).toBe("49,00 US$");
    expect(crearFormato("en", "UTC").moneda(49, "USD")).toBe("$49.00");
    expect(n(crearFormato("es", "UTC").moneda(49000, "CLP"))).toBe("49.000 CLP");
    expect(n(crearFormato("en", "UTC").moneda(12.5, "EUR"))).toBe("€12.50");
    // Un código que no es ISO 4217 no se inventa un símbolo.
    expect(crearFormato("en", "UTC").moneda(10, "XX")).toBe("10 XX");
  });

  it("tamaños de archivo en base 1024", () => {
    const f = crearFormato("es", "UTC");
    expect(n(f.bytes(512))).toBe("512 B");
    // 1000 bytes todavía no son 1 kB en base 1024.
    expect(n(f.bytes(1000))).toBe("1000 B");
    expect(n(f.bytes(820 * 1024))).toBe("820 kB");
    expect(n(f.bytes(25 * 1024 * 1024))).toBe("25 MB");
    expect(n(crearFormato("en", "UTC").bytes(1.5 * 1024 * 1024))).toBe("1.5 MB");
  });
});

describe("fechas de calendario", () => {
  it("YYYY-MM-DD es ese día en cualquier zona (no el anterior al oeste de Greenwich)", () => {
    expect(n(crearFormato("es", "America/Santiago").dia("2026-12-01"))).toBe("1 dic 2026");
    expect(n(crearFormato("es", "Pacific/Honolulu").dia("2026-12-01"))).toBe("1 dic 2026");
    expect(crearFormato("en", "America/Santiago").dia("2026-12-01")).toBe("Dec 1, 2026");
  });

  it("con hora, el día en la zona de la organización", () => {
    expect(n(crearFormato("es", "Europe/Madrid").dia("2026-11-21T23:30:00Z"))).toBe("22 nov 2026");
    expect(n(crearFormato("es", "America/Santiago").dia("2026-11-21T23:30:00Z"))).toBe(
      "21 nov 2026",
    );
  });

  it("sin fecha, «—»", () => {
    expect(crearFormato("es", "UTC").dia(null)).toBe("—");
    expect(crearFormato("es", "UTC").dia("2026-13-45")).toBe("—");
  });
});
