/** Contexto de autenticación tipado (KC-PT-03-4, cierra PT-F19). */
import { renderHook, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { perfilOwner, sesion } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";
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

describe("acceso restringido", () => {
  it("lo fija la respuesta de login y no se pierde al renovar (/users/me no lo informa)", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () =>
        HttpResponse.json({ ...sesion, acceso_restringido: true }),
      ),
    );
    await entrarDesde("/tickets");
    await waitFor(() => expect(rutaActual()).toBe("/consumo"));

    // 401 → renovación → nueva verificación de /users/me, que no trae acceso_restringido.
    server.use(
      http.get("*/api/v1/users/me", () =>
        HttpResponse.json({ ...perfilOwner, nombre: "Olga Renovada" }),
      ),
      http.get("*/api/v1/organization", ({ request }) =>
        request.headers.get("Authorization") === "Bearer acc_1"
          ? sobreError(401, "NO_AUTENTICADO", "El token ha expirado.")
          : HttpResponse.json({}),
      ),
    );
    await api.get("/api/v1/organization");
    // El perfil renovado ya se aplicó: lo que siga a la verificación también.
    await screen.findByTitle("Olga Renovada");

    expect(screen.getByText(/suscripción suspendida/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Tickets/ })).not.toBeInTheDocument();
    expect(rutaActual()).toBe("/consumo");
  });
});
