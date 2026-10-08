/** Contexto de autenticación tipado (KC-PT-03-4, cierra PT-F19). */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAuth } from "./auth";

describe("useAuth", () => {
  it("fuera de <AuthProvider> falla con un mensaje claro", () => {
    // React registra el error lanzado durante el render; aquí es lo esperado.
    const consola = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => renderHook(() => useAuth())).toThrow(
      "useAuth() debe usarse dentro de <AuthProvider>.",
    );
    consola.mockRestore();
  });
});
