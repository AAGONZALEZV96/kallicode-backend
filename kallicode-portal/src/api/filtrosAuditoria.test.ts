/** Filtros del explorador de auditoría (KC-PT-08-5): saneo, rango de fechas y parámetros. */
import { describe, expect, it } from "vitest";

import {
  CLAVES_FILTRO,
  errorDeRango,
  hayFiltros,
  parametrosConsulta,
  sanear,
  type FiltrosUrl,
} from "./filtrosAuditoria";

const vacios = Object.fromEntries(CLAVES_FILTRO.map((k) => [k, ""])) as FiltrosUrl;
const con = (f: Partial<FiltrosUrl>): FiltrosUrl => ({ ...vacios, ...f });

describe("sanear", () => {
  it("conserva los valores válidos", () => {
    const f = con({
      q: " gate ",
      actor: "agente",
      ticket: " KC-1042 ",
      etapa: "build",
      desde: "2026-11-01",
      hasta: "2026-11-20",
      orden: "antiguos",
    });
    expect(sanear(f)).toEqual({ ...f, q: "gate", ticket: "KC-1042" });
  });

  it("descarta lo que el backend rechazaría (un enlace viejo o editado a mano)", () => {
    expect(
      sanear(
        con({
          actor: "root",
          etapa: "borrar",
          desde: "ayer",
          hasta: "2026-02-31x",
          orden: "-id; DROP",
          q: "x".repeat(250),
          ticket: "K".repeat(50),
        }),
      ),
    ).toEqual(con({ q: "x".repeat(200), ticket: "K".repeat(40) }));
  });

  it("una fecha con formato correcto pero inexistente no pasa", () => {
    expect(sanear(con({ desde: "2026-13-40" })).desde).toBe("");
  });
});

describe("errorDeRango", () => {
  it.each([
    ["", "2026-11-20", undefined],
    ["2026-11-20", "2026-11-20", undefined],
    ["2026-11-20", "2026-11-19", "invertido"],
    ["2026-01-01", "2026-04-03", undefined], // 92 días: el máximo del backend
    ["2026-01-01", "2026-04-04", "excesivo"], // 93 días
  ])("desde %s hasta %s → %s", (desde, hasta, esperado) => {
    expect(errorDeRango(desde, hasta)).toBe(esperado);
  });
});

describe("parametrosConsulta", () => {
  it("traduce los nombres de la URL a los de GET /audit/events", () => {
    const p = parametrosConsulta(
      con({
        q: "gate",
        actor: "agente",
        ticket: "KC-1042",
        etapa: "build",
        desde: "2026-11-01",
        hasta: "2026-11-20",
        orden: "antiguos",
      }),
      3,
    );

    expect(Object.fromEntries(p)).toEqual({
      page: "3",
      page_size: "50",
      q: "gate",
      actor_tipo: "agente",
      ticket_id: "KC-1042",
      etapa: "build",
      // Días completos en la zona del navegador: «hasta» incluye todo ese día.
      desde: new Date("2026-11-01T00:00:00").toISOString(),
      hasta: new Date("2026-11-20T23:59:59.999").toISOString(),
      sort: "creado_en",
    });
  });

  it.each([
    ["etapa", "etapa"],
    ["actor", "actor_tipo"],
  ])("el orden %s es sort=%s", (orden, sort) => {
    expect(parametrosConsulta(con({ orden }), 1).get("sort")).toBe(sort);
  });

  it("sin filtros manda solo la página", () => {
    expect(parametrosConsulta(vacios, 1).toString()).toBe("page=1&page_size=50");
  });
});

it("hayFiltros", () => {
  expect(hayFiltros(vacios)).toBe(false);
  expect(hayFiltros(con({ orden: "antiguos" }))).toBe(true);
});
