/** Matriz de control de acceso (PT-F06): debe coincidir con los requiere_rol del backend. */
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { MAPA_RUTAS_ROLES, puedeVerRuta, rolActual } from "./auth";

const TODOS: Rol[] = ["owner", "admin", "architect", "approver", "member", "viewer"];

/** Roles esperados por ruta, con el endpoint del backend que los fija. */
const ESPERADO: Record<string, Rol[]> = {
  "/": TODOS,
  "/tickets": TODOS,
  "/tickets/:numero": TODOS,
  // tickets.ROLES_CREAN
  "/nuevo-ticket": ["owner", "admin", "architect", "approver", "member"],
  "/nueva-funcionalidad": ["owner", "admin", "architect", "approver", "member"],
  // gates._ROLES_APROBADOR
  "/aprobaciones": ["owner", "admin", "architect", "approver"],
  // audit._ROLES_AUDIT; el expediente suma approver
  "/auditoria": ["owner", "admin", "architect"],
  "/auditoria/:numero": ["owner", "admin", "architect", "approver"],
  // connections: requiere_rol("owner", "admin")
  "/conexiones": ["owner", "admin"],
  // models_ia GET: requiere_rol("owner", "admin", "architect")
  "/modelos": ["owner", "admin", "architect"],
  "/consumo": ["owner", "admin", "architect", "member"],
};

describe("MAPA_RUTAS_ROLES", () => {
  it("declara exactamente las 11 rutas con vista", () => {
    expect(Object.keys(MAPA_RUTAS_ROLES).sort()).toEqual(Object.keys(ESPERADO).sort());
  });

  it("solo usa los nombres de rol del backend", () => {
    for (const roles of Object.values(MAPA_RUTAS_ROLES)) {
      for (const rol of roles) expect(TODOS).toContain(rol);
    }
  });

  describe.each(Object.entries(ESPERADO))("%s", (ruta, permitidos) => {
    it.each(TODOS)("%s", (rol) => {
      expect(puedeVerRuta(rol, ruta)).toBe(permitidos.includes(rol));
    });
  });
});

describe("puedeVerRuta y rolActual", () => {
  it("una ruta fuera del mapa no da acceso", () => {
    expect(puedeVerRuta("owner", "/organizacion")).toBe(false);
  });

  it("sin usuario cargado se asume viewer", () => {
    expect(rolActual(null)).toBe("viewer");
  });
});
