/** Recorrido de humo: login contra los dobles MSW, llegada al tablero y ciclo de sesión. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { api, haySesion } from "./api/client";
import { perfilOwner } from "./mocks/datos";
import { sobreError } from "./mocks/handlers";
import { server } from "./mocks/server";
import { entrarDesde, renderPortal, rutaActual } from "./test/portal";

describe("portal", () => {
  it("inicia sesión y muestra el tablero con el kanban", async () => {
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /KC-1001/ })).toHaveAttribute(
      "href",
      "/tickets/KC-1001",
    );
    // Ni tokens ni perfil quedan en el navegador (KC-PT-02-2).
    expect(sessionStorage.length).toBe(0);
  });

  it("«Salir» vuelve al login y revoca la sesión en el servidor (KC-PT-02-3)", async () => {
    const revocados: string[] = [];
    server.use(
      http.post("*/api/v1/auth/logout", async ({ request }) => {
        const { refresh_token } = (await request.json()) as { refresh_token: string };
        revocados.push(refresh_token);
        return HttpResponse.json({ cerradas: 1 });
      }),
    );
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal();
    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));
    await screen.findByRole("heading", { name: "Tablero de avance" });

    await usuario.click(screen.getByRole("button", { name: "Salir" }));

    expect(await screen.findByRole("button", { name: "Entrar" })).toBeInTheDocument();
    await waitFor(() => expect(revocados).toEqual(["ref_1"]));
    expect(haySesion()).toBe(false);
  });

  it("un enlace directo sin sesión pasa por /login y, al entrar, vuelve a la ruta pedida", async () => {
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal("/tickets/KC-1001");
    expect(rutaActual()).toBe("/login?siguiente=%2Ftickets%2FKC-1001");

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "Dev12345!demo");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(
      await screen.findByRole("heading", { name: "Timeout en búsqueda de clientes" }),
    ).toBeInTheDocument();
    expect(rutaActual()).toBe("/tickets/KC-1001");
  });

  it.each(["https://malo.example/robar", "//malo.example/robar"])(
    "ignora un siguiente hacia otro sitio (%s) y lleva al tablero",
    async (externo) => {
      await entrarDesde(`/login?siguiente=${encodeURIComponent(externo)}`);

      expect(await screen.findByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
      expect(rutaActual()).toBe("/");
    },
  );

  it("usa el perfil verificado en /users/me, no solo el de la respuesta de login", async () => {
    server.use(
      http.get("*/api/v1/users/me", () =>
        HttpResponse.json({ ...perfilOwner, nombre: "Olga Verificada" }),
      ),
    );

    await entrarDesde();

    expect(await screen.findByTitle("Olga Verificada")).toBeInTheDocument();
  });

  it("si /users/me falla, no entra y revoca la sesión recién abierta", async () => {
    const revocados: string[] = [];
    server.use(
      http.get("*/api/v1/users/me", () => sobreError(503, "SERVICIO_NO_DISPONIBLE", "Caído.")),
      http.post("*/api/v1/auth/logout", async ({ request }) => {
        const { refresh_token } = (await request.json()) as { refresh_token: string };
        revocados.push(refresh_token);
        return HttpResponse.json({ cerradas: 1 });
      }),
    );

    await entrarDesde();

    expect(await screen.findByText("SERVICIO_NO_DISPONIBLE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Entrar" })).toBeInTheDocument();
    expect(revocados).toEqual(["ref_1"]);
    expect(haySesion()).toBe(false);
  });

  it("tras renovar el token vuelve a verificar el perfil (p. ej. un cambio de nombre o rol)", async () => {
    await entrarDesde();
    await screen.findByTitle("Olga Owner");

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
    await api.get("/api/v1/organization"); // 401 → renovación → acc_2

    expect(await screen.findByTitle("Olga Renovada")).toBeInTheDocument();
  });

  it("si la sesión expira, al volver a entrar regresa a la misma página", async () => {
    await entrarDesde("/aprobaciones");
    await screen.findByRole("heading", { name: "Aprobaciones pendientes" });

    server.use(
      http.get("*/api/v1/organization", () => sobreError(401, "NO_AUTENTICADO", "Expirado.")),
      http.post("*/api/v1/auth/refresh", () => sobreError(401, "AUTH_REFRESH_INVALIDO", "No.")),
    );
    await api.get("/api/v1/organization").catch(() => undefined);

    await waitFor(() => expect(rutaActual()).toBe("/login?siguiente=%2Faprobaciones"));
  });

  it("con credenciales inválidas muestra el código de error y el trace_id", async () => {
    const usuario = userEvent.setup();
    const { correo, clave } = renderPortal();

    await usuario.type(correo, "owner@demo.kallicode.dev");
    await usuario.type(clave, "incorrecta123");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByText("AUTH_CREDENCIALES_INVALIDAS")).toBeInTheDocument();
    expect(screen.getByText(/tr_msw_prueba/)).toBeInTheDocument();
  });
});
