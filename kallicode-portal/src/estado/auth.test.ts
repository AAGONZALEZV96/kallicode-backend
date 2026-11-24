/** Matriz de control de acceso (PT-F06): debe coincidir con los requiere_rol del backend. */
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { type Accion, MAPA_RUTAS_ROLES, permiteAccion, puedeVerRuta, rolActual } from "./auth";

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

  // connections.py (onboarding): solo owner y admin (KC-PT-07-1)
  "/onboarding": ["owner", "admin"],
  "/conexiones": ["owner", "admin"],
  // models_ia GET: requiere_rol("owner", "admin", "architect")
  "/modelos": ["owner", "admin", "architect"],
  "/consumo": ["owner", "admin", "architect", "member"],
  // KC-PT-10-4
  "/perfil": TODOS,
  "/organizacion": ["admin", "owner"],
};

describe("MAPA_RUTAS_ROLES", () => {
  it("declara exactamente las 14 rutas con vista", () => {
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
    expect(puedeVerRuta("owner", "/no-existe")).toBe(false);
  });

  it("sin usuario cargado se asume viewer", () => {
    expect(rolActual(null)).toBe("viewer");
  });
});

/** Acciones de las vistas (KC-PT-03-2): quién puede, según el endpoint del backend. */
const ACCIONES: [string, Accion, Rol[]][] = [
  // gates._ROLES_APROBADOR / seguridad.ROLES_FIRMA_G12
  ["firmar Gate 1", { tipo: "firmar-gate", gate: 1 }, ["owner", "admin", "architect", "approver"]],
  ["firmar Gate 2", { tipo: "firmar-gate", gate: 2 }, ["owner", "admin", "architect", "approver"]],
  // seguridad.ROLES_FIRMA_G3
  ["firmar Gate 3", { tipo: "firmar-gate", gate: 3 }, ["architect"]],
  // tickets.actualizar: el creador o un administrador
  [
    "cancelar un ticket ajeno",
    { tipo: "cancelar-ticket", creadorId: "u_otro" },
    ["owner", "admin"],
  ],
  ["cancelar el propio ticket", { tipo: "cancelar-ticket", creadorId: "u_yo" }, TODOS],
  ["cancelar sin creador", { tipo: "cancelar-ticket", creadorId: null }, ["owner", "admin"]],
  // POST /connections/{id}/test
  ["probar una conexión", { tipo: "probar-conexion" }, ["owner", "admin"]],
  // POST y PATCH /connections; DELETE solo el owner
  ["crear o renombrar una conexión", { tipo: "gestionar-conexion" }, ["owner", "admin"]],
  ["eliminar una conexión", { tipo: "eliminar-conexion" }, ["owner"]],
  // POST /onboarding/complete
  ["encender la fábrica", { tipo: "encender-fabrica" }, ["owner"]],
  // POST /plans/upgrade-request
  ["solicitar upgrade", { tipo: "solicitar-upgrade" }, ["owner", "admin"]],
  // POST /audit/exports: audit._ROLES_AUDIT (el approver ve el expediente, pero no exporta)
  ["exportar auditoría", { tipo: "exportar-auditoria" }, ["owner", "admin", "architect"]],
  // GET /audit/integrity: audit._ROLES_AUDIT (el approver ve la verificación del expediente)
  [
    "volver a verificar los sellos",
    { tipo: "verificar-integridad" },
    ["owner", "admin", "architect"],
  ],
  // GET /audit/steps/{id}: audit._ROLES_AUDIT (el approver ve los pasos, no sus trazas)
  ["inspeccionar un paso LLM", { tipo: "inspeccionar-paso" }, ["owner", "admin", "architect"]],
  // tickets.add_watcher: ROLES_CREAN (todos menos viewer)
  [
    "seguir un ticket",
    { tipo: "agregar-visualizador" },
    ["owner", "admin", "architect", "approver", "member"],
  ],
  // agregar a otra persona exige listar usuarios (users.listar)
  [
    "elegir a otra persona",
    { tipo: "elegir-visualizador" },
    ["owner", "admin", "architect", "approver"],
  ],
  // tickets.del_watcher: uno mismo, un administrador o el creador; al creador, nadie
  [
    "quitarse a sí mismo",
    { tipo: "quitar-visualizador", visualizadorId: "u_yo", creadorId: "u_otro" },
    TODOS,
  ],
  [
    "quitar a otra persona",
    { tipo: "quitar-visualizador", visualizadorId: "u_x", creadorId: "u_otro" },
    ["owner", "admin"],
  ],
  [
    "quitar a otra persona siendo el creador",
    { tipo: "quitar-visualizador", visualizadorId: "u_x", creadorId: "u_yo" },
    TODOS,
  ],
  [
    "quitar al creador",
    { tipo: "quitar-visualizador", visualizadorId: "u_otro", creadorId: "u_otro" },
    [],
  ],
  [
    "quitarse siendo el creador",
    { tipo: "quitar-visualizador", visualizadorId: "u_yo", creadorId: "u_yo" },
    [],
  ],
];

describe("permiteAccion", () => {
  describe.each(ACCIONES)("%s", (_nombre, accion, permitidos) => {
    it.each(TODOS)("%s", (rol) => {
      expect(permiteAccion({ id: "u_yo", rol }, accion)).toBe(permitidos.includes(rol));
    });
  });

  it("sin usuario no permite nada", () => {
    expect(permiteAccion(null, { tipo: "solicitar-upgrade" })).toBe(false);
  });

  it.each(ACCIONES.filter(([, a]) => a.tipo !== "solicitar-upgrade"))(
    "con la suscripción suspendida no permite %s",
    (_nombre, accion) => {
      expect(permiteAccion({ id: "u_yo", rol: "owner" }, accion, true)).toBe(false);
    },
  );

  it("con la suscripción suspendida el owner aún puede solicitar upgrade", () => {
    expect(permiteAccion({ id: "u_yo", rol: "owner" }, { tipo: "solicitar-upgrade" }, true)).toBe(
      true,
    );
  });
});
