/**
 * Diccionarios bajo demanda (KC-PT-11-1): el español va siempre; el inglés y el portugués se
 * descargan al elegirlos. Cada prueba usa un módulo i18n recién cargado, sin los diccionarios
 * que test/setup.ts precarga para el resto de las pruebas.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

async function i18nNuevo() {
  vi.resetModules();
  const modulo = await import("./i18n");
  const envoltorio = ({ children }: { children: ReactNode }) => (
    <modulo.I18nProvider>{children}</modulo.I18nProvider>
  );
  return { ...modulo, envoltorio };
}

beforeEach(() => localStorage.clear());

describe("diccionarios bajo demanda", () => {
  it("sin idioma guardado (o con uno desconocido) se usa el español, sin descargar nada", async () => {
    localStorage.setItem("kc_idioma", "fr");
    const { useI18n, envoltorio, idiomaGuardado } = await i18nNuevo();
    expect(idiomaGuardado()).toBe("es");
    const { result } = renderHook(() => useI18n(), { wrapper: envoltorio });
    expect(result.current.idioma).toBe("es");
    expect(result.current.t("nav.dashboard")).toBe("Tablero");
  });

  it("el idioma guardado que no se precargó se descarga y la interfaz cambia al llegar", async () => {
    localStorage.setItem("kc_idioma", "en");
    const { useI18n, envoltorio } = await i18nNuevo();
    const { result } = renderHook(() => useI18n(), { wrapper: envoltorio });
    // Mientras se descarga se ve en español (main.tsx lo evita descargándolo antes de pintar).
    expect(result.current.idioma).toBe("es");
    await waitFor(() => expect(result.current.t("nav.dashboard")).toBe("Dashboard"));
    expect(document.documentElement.lang).toBe("en");
  });

  it("precargado (como hace main.tsx), se pinta directamente en ese idioma", async () => {
    localStorage.setItem("kc_idioma", "pt");
    const { useI18n, envoltorio, cargarIdioma, idiomaGuardado } = await i18nNuevo();
    await cargarIdioma(idiomaGuardado());
    const { result } = renderHook(() => useI18n(), { wrapper: envoltorio });
    expect(result.current.idioma).toBe("pt");
    expect(result.current.t("nav.dashboard")).toBe("Painel");
  });

  it("al elegir dos idiomas seguidos gana el último, aunque el primero llegue después", async () => {
    const { useI18n, envoltorio } = await i18nNuevo();
    const { result } = renderHook(() => useI18n(), { wrapper: envoltorio });
    act(() => {
      result.current.cambiarIdioma("en");
      result.current.cambiarIdioma("pt");
    });
    await waitFor(() => expect(result.current.idioma).toBe("pt"));
    // Se esperan las dos descargas: la del inglés no debe pisar la elección.
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current.idioma).toBe("pt");
    expect(localStorage.getItem("kc_idioma")).toBe("pt");
  });
});
