/** Almacenamiento de borradores (KC-PT-11-6). */
import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  borrarBorrador,
  borrarTodosLosBorradores,
  guardarBorrador,
  leerBorrador,
  VIGENCIA_BORRADOR_MS,
} from "./borradores";

const ANA = { id: "u_ana", org_id: "org_a" };
const ESQUEMA = z.object({ titulo: z.string() });

describe("borradores", () => {
  it("guarda y lee el borrador de una persona y un formulario", () => {
    guardarBorrador(ANA, "nuevo-ticket", { titulo: "Exportar a CSV" });
    const leido = leerBorrador(ANA, "nuevo-ticket", ESQUEMA);
    expect(leido?.datos).toEqual({ titulo: "Exportar a CSV" });
    expect(leido?.guardado).toBeInstanceOf(Date);
  });

  it("cada persona, organización y formulario tiene el suyo", () => {
    guardarBorrador(ANA, "nuevo-ticket", { titulo: "de Ana" });
    expect(leerBorrador({ id: "u_otro", org_id: "org_a" }, "nuevo-ticket", ESQUEMA)).toBeNull();
    expect(leerBorrador({ id: "u_ana", org_id: "org_b" }, "nuevo-ticket", ESQUEMA)).toBeNull();
    expect(leerBorrador(ANA, "nueva-funcionalidad", ESQUEMA)).toBeNull();
  });

  it("uno con otra forma (viejo o editado a mano) o con JSON roto se ignora", () => {
    guardarBorrador(ANA, "nuevo-ticket", { titulo: 42 });
    expect(leerBorrador(ANA, "nuevo-ticket", ESQUEMA)).toBeNull();
    localStorage.setItem("kc_borrador:org_a:u_ana:nuevo-ticket", "{no es json");
    expect(leerBorrador(ANA, "nuevo-ticket", ESQUEMA)).toBeNull();
  });

  it("vence a los 7 días", () => {
    guardarBorrador(ANA, "nuevo-ticket", { titulo: "viejo" });
    const ahora = Date.now();
    expect(
      leerBorrador(ANA, "nuevo-ticket", ESQUEMA, ahora + VIGENCIA_BORRADOR_MS - 60_000),
    ).not.toBeNull();
    expect(
      leerBorrador(ANA, "nuevo-ticket", ESQUEMA, ahora + VIGENCIA_BORRADOR_MS + 60_000),
    ).toBeNull();
  });

  it("se borra uno, o todos al cerrar sesión (sin tocar el resto de localStorage)", () => {
    guardarBorrador(ANA, "nuevo-ticket", { titulo: "a" });
    guardarBorrador(ANA, "nueva-funcionalidad", { titulo: "b" });
    localStorage.setItem("kc_idioma", "en");
    borrarBorrador(ANA, "nuevo-ticket");
    expect(leerBorrador(ANA, "nuevo-ticket", ESQUEMA)).toBeNull();
    expect(leerBorrador(ANA, "nueva-funcionalidad", ESQUEMA)).not.toBeNull();
    borrarTodosLosBorradores();
    expect(leerBorrador(ANA, "nueva-funcionalidad", ESQUEMA)).toBeNull();
    expect(localStorage.getItem("kc_idioma")).toBe("en");
  });
});
