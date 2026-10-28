/** Diccionario i18n: permisos y suscripción en los tres idiomas (KC-PT-03-6). */
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ROLES, type UsageSummary } from "../api/tipos";
import { DICT, I18nProvider, useI18n, type Idioma } from "./i18n";

const IDIOMAS: Idioma[] = ["es", "en", "pt"];
/** core.subscriptions.estado (sub_estado_chk). */
const ESTADOS_SUSCRIPCION = ["activa", "periodo_gracia", "suspendida", "finalizada"];
const UMBRALES: UsageSummary["umbral"][] = ["normal", "aviso_80", "agotado"];
/** Denegaciones 403 del backend (kallicode_core.errors y routers). */
const DENEGACIONES = ["PERMISO_DENEGADO", "FUNCION_NO_DISPONIBLE", "FIRMA_NO_AUTORIZADA"];

const CLAVES = [
  ...ROLES.map((r) => `rol.${r}`),
  ...ESTADOS_SUSCRIPCION.map((e) => `sub.estado.${e}`),
  ...UMBRALES.map((u) => `cu.umbral.${u}`),
  ...DENEGACIONES.map((c) => `err.${c}`),
  "acceso.rol",
  "sub.banner.suspendida",
  "cu.umbral",
];

describe("diccionario de permisos y suscripción", () => {
  describe.each(IDIOMAS)("%s", (idioma) => {
    it.each(CLAVES)("%s", (clave) => {
      expect(DICT[idioma][clave]?.trim()).toBeTruthy();
    });

    it("el aviso de rol lleva el marcador {rol}", () => {
      expect(DICT[idioma]["acceso.rol"]).toContain("{rol}");
    });
  });

  it("en y pt tienen exactamente las claves de es", () => {
    const base = Object.keys(DICT.es).sort();
    expect(Object.keys(DICT.en).sort()).toEqual(base);
    expect(Object.keys(DICT.pt).sort()).toEqual(base);
  });
});

describe("t()", () => {
  function tEn(idioma: Idioma) {
    localStorage.setItem("kc_idioma", idioma);
    const envoltorio = ({ children }: { children: ReactNode }) => (
      <I18nProvider>{children}</I18nProvider>
    );
    return renderHook(() => useI18n(), { wrapper: envoltorio }).result.current.t;
  }

  it("reemplaza los marcadores con los valores", () => {
    const t = tEn("en");
    expect(t("acceso.rol", { rol: t("rol.approver") })).toBe(
      "Your role (Approver) can't access that section. We took you to the dashboard.",
    );
  });

  it("deja intacto un marcador sin valor", () => {
    expect(tEn("es")("acceso.rol", {})).toContain("{rol}");
  });
});
