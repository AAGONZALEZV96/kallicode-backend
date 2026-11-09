/** Enlaces firmados de la evidencia QA (KC-PT-05-2): cuándo se conserva el anterior. */
import { describe, expect, it } from "vitest";

import { conservarEnlaces, MARGEN_ENLACE_MS, vencido } from "./enlacesEvidencia";
import type { Evidencia } from "./tipos";

const AHORA = Date.parse("2026-11-08T12:00:00Z");
const en = (ms: number) => new Date(AHORA + ms).toISOString();

type Archivo = Evidencia["matriz"][number]["evidencias"][number];

const archivo = (blob_ref: string, firma: string, expira_en: string | null): Archivo => ({
  tipo: "screenshot",
  blob_ref,
  download_url: `https://blob.test/${blob_ref}?sig=${firma}`,
  expira_en,
});

const respuesta = (...evidencias: Archivo[]): Evidencia => ({
  corrida: 1,
  ejecutada_en: "2026-11-08T11:00:00Z",
  matriz: [
    { criterio_id: "C1", resultado: "pasa", extracto_fallo: null, evidencias: [] },
    { criterio_id: "C2", resultado: "falla", extracto_fallo: "x", evidencias },
  ],
  regresion: { dirigidos: 1, dirigidos_ok: 0, suite_cliente: 2, cliente_ok: 2 },
  flaky: [],
});

const enlaces = (datos: unknown) =>
  (datos as Evidencia).matriz[1]!.evidencias.map((a) => a.download_url);

describe("conservarEnlaces", () => {
  it("mantiene el enlace anterior mientras le quede más que el margen", () => {
    const previos = respuesta(archivo("c2.png", "vieja", en(5 * 60_000)));
    const nuevos = respuesta(archivo("c2.png", "nueva", en(10 * 60_000)));

    const resultado = conservarEnlaces(previos, nuevos, AHORA) as Evidencia;

    expect(enlaces(resultado)).toEqual(["https://blob.test/c2.png?sig=vieja"]);
    expect(resultado.matriz[1]!.evidencias[0]!.expira_en).toBe(en(5 * 60_000));
    // Nada cambió: TanStack recibe el mismo objeto y no vuelve a pintar.
    expect(resultado).toBe(previos);
  });

  it("cambia al enlace nuevo cuando al anterior le queda menos del margen", () => {
    const previos = respuesta(archivo("c2.png", "vieja", en(MARGEN_ENLACE_MS - 1)));
    const nuevos = respuesta(archivo("c2.png", "nueva", en(10 * 60_000)));

    expect(enlaces(conservarEnlaces(previos, nuevos, AHORA))).toEqual([
      "https://blob.test/c2.png?sig=nueva",
    ]);
  });

  it("sin fecha de vencimiento no reutiliza el enlace anterior", () => {
    const previos = respuesta(archivo("c2.png", "vieja", null));
    const nuevos = respuesta(archivo("c2.png", "nueva", null));

    expect(enlaces(conservarEnlaces(previos, nuevos, AHORA))).toEqual([
      "https://blob.test/c2.png?sig=nueva",
    ]);
  });

  it("una evidencia nueva usa su propio enlace y la ya conocida conserva el suyo", () => {
    const previos = respuesta(archivo("c2.png", "vieja", en(5 * 60_000)));
    const nuevos = respuesta(
      archivo("c2.png", "nueva", en(10 * 60_000)),
      archivo("c2.webm", "nueva", en(10 * 60_000)),
    );

    expect(enlaces(conservarEnlaces(previos, nuevos, AHORA))).toEqual([
      "https://blob.test/c2.png?sig=vieja",
      "https://blob.test/c2.webm?sig=nueva",
    ]);
  });

  it("si la respuesta nueva ya no trae enlace, no revive el anterior", () => {
    const previos = respuesta(archivo("c2.png", "vieja", en(5 * 60_000)));
    const nuevos = respuesta({ tipo: "screenshot", blob_ref: "c2.png" });

    expect(enlaces(conservarEnlaces(previos, nuevos, AHORA))).toEqual([undefined]);
  });

  it("sin blob_ref ni adjunto_id no hay cómo reconocerla: usa el enlace nuevo", () => {
    const sinClave = (firma: string): Archivo => ({
      tipo: "video",
      download_url: `https://blob.test/v?sig=${firma}`,
      expira_en: en(10 * 60_000),
    });

    expect(
      enlaces(conservarEnlaces(respuesta(sinClave("vieja")), respuesta(sinClave("nueva")), AHORA)),
    ).toEqual(["https://blob.test/v?sig=nueva"]);
  });

  it("las demás respuestas (otra pestaña o 404 → null) pasan como siempre", () => {
    const nuevos = respuesta(archivo("c2.png", "nueva", en(10 * 60_000)));

    expect(conservarEnlaces(null, nuevos, AHORA)).toBe(nuevos);
    expect(conservarEnlaces(nuevos, null, AHORA)).toBeNull();
    expect(conservarEnlaces({ a: 1 }, { a: 1 }, AHORA)).toEqual({ a: 1 });
  });
});

describe("vencido", () => {
  it.each([
    ["en el pasado", en(-1), true],
    ["justo ahora", en(0), true],
    ["en el futuro", en(1), false],
    ["sin fecha", null, false],
    ["con una fecha inválida", "pronto", false],
  ])("%s → %s", (_caso, expira, esperado) => {
    expect(vencido(expira, AHORA)).toBe(esperado);
  });
});
