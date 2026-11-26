/** Guardas de tipo sin aserciones (KC-PT-11-3). */
import { describe, expect, it } from "vitest";

import { esUnoDe, registroDe } from "./tipado";

const ROLES = ["owner", "admin", "viewer"] as const;

describe("esUnoDe", () => {
  it("acepta solo valores de la lista (y estrecha el tipo)", () => {
    const valor: string = "admin";
    expect(esUnoDe(ROLES, valor)).toBe(true);
    if (esUnoDe(ROLES, valor)) {
      const rol: (typeof ROLES)[number] = valor;
      expect(rol).toBe("admin");
    }
    expect(esUnoDe(ROLES, "root")).toBe(false);
    expect(esUnoDe(ROLES, "")).toBe(false);
    expect(esUnoDe(ROLES, null)).toBe(false);
    expect(esUnoDe(ROLES, undefined)).toBe(false);
  });
});

describe("registroDe", () => {
  it("arma una entrada por clave", () => {
    expect(registroDe(ROLES, (r) => r.length)).toEqual({ owner: 5, admin: 5, viewer: 6 });
  });

  it("sin claves, un registro vacío", () => {
    expect(registroDe([] as const, () => 1)).toEqual({});
  });
});
