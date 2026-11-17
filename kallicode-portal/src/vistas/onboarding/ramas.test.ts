/** Validación del mapa de ramas (KC-PT-07-3). */
import { describe, expect, it } from "vitest";

import { errorDeRama, limpiar, MAPA_SUGERIDO, type CampoRama, type MapaRamas } from "./ramas";

const con = (cambios: Partial<MapaRamas>): MapaRamas => ({ ...MAPA_SUGERIDO, ...cambios });
const error = (campo: CampoRama, valor: string) => errorDeRama(campo, con({ [campo]: valor }));

describe("errorDeRama", () => {
  it("el mapa sugerido es válido", () => {
    for (const campo of ["feature", "staging", "produccion"] as const) {
      expect(errorDeRama(campo, MAPA_SUGERIDO)).toBeUndefined();
    }
  });

  it.each(["release/2026.11", "hotfix-1", "team/a/b", "v1.0", "main"])(
    "«%s» es un nombre de rama válido",
    (v) => expect(error("staging", v)).toBeUndefined(),
  );

  it("vacío o solo espacios es obligatorio", () => {
    expect(error("staging", "  ")).toBe("requerido");
  });

  it("más de 100 caracteres supera el largo del backend", () => {
    expect(error("feature", "a".repeat(100))).toBeUndefined();
    expect(error("feature", "a".repeat(101))).toBe("max");
  });

  it.each([
    "mi rama",
    "a..b",
    "a~1",
    "a^",
    "a:b",
    "a?",
    "a[1]",
    "a\\b",
    "/main",
    "main/",
    "main.",
    "a//b",
    "main.lock",
    "a.lock/b",
    ".oculta",
    "a/.b",
    "a@{1}",
    "@",
    "a\tb",
    "a\u0001b",
    "a\u007fb",
  ])("«%s» no es un nombre de rama", (v) => {
    expect(error("staging", v)).toBe("formato");
  });

  it("el comodín solo vale en el patrón de ramas de trabajo", () => {
    expect(error("feature", "feat/*")).toBeUndefined();
    expect(error("feature", "*")).toBeUndefined();
    expect(error("staging", "dev*")).toBe("comodin");
    expect(error("produccion", "*")).toBe("comodin");
    // El patrón sigue las demás reglas.
    expect(error("feature", "feat /*")).toBe("formato");
  });

  it("producción no puede ser igual a staging (comparando sin espacios)", () => {
    expect(errorDeRama("produccion", con({ staging: " main", produccion: "main " }))).toBe(
      "iguales",
    );
    // El error va en producción, no en staging.
    expect(errorDeRama("staging", con({ staging: "main", produccion: "main" }))).toBeUndefined();
  });
});

it("limpiar quita los espacios alrededor de cada campo", () => {
  expect(limpiar({ feature: " feat/* ", staging: "dev ", produccion: " prod" })).toEqual({
    feature: "feat/*",
    staging: "dev",
    produccion: "prod",
  });
});
