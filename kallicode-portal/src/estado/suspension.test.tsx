/**
 * Cuenta con la suscripción suspendida contra un doble fiel al backend (KC-PT-03-3, PT-F07):
 * /users/me y /notifications responden 403, y cada /auth/refresh informa acceso_restringido.
 */
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { api } from "../api/client";
import { sesion } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

function backendConSuscripcion({ suspendida }: { suspendida: boolean }) {
  const estado = { suspendida };
  const cuenta = { perfiles: 0, notificaciones: 0 };
  const denegado = () =>
    sobreError(
      403,
      "PERMISO_DENEGADO",
      "Suscripción suspendida: acceso limitado a Consumo y plan.",
    );
  server.use(
    http.post("*/api/v1/auth/login", () =>
      HttpResponse.json({ ...sesion, acceso_restringido: estado.suspendida }),
    ),
    http.post("*/api/v1/auth/refresh", () =>
      HttpResponse.json({
        access_token: "acc_2",
        refresh_token: "ref_2",
        acceso_restringido: estado.suspendida,
      }),
    ),
    // Sin return: con la cuenta activa siguen los dobles generales.
    http.get("*/api/v1/users/me", () => {
      cuenta.perfiles++;
      if (estado.suspendida) return denegado();
    }),
    http.get("*/api/v1/notifications", () => {
      cuenta.notificaciones++;
      if (estado.suspendida) return denegado();
    }),
  );
  return { estado, cuenta };
}

/** Fuerza una renovación: /organization (permitido aun suspendida) rechaza el primer access. */
async function renovar() {
  server.use(
    http.get("*/api/v1/organization", ({ request }) =>
      request.headers.get("Authorization") === "Bearer acc_1"
        ? sobreError(401, "NO_AUTENTICADO", "El token ha expirado.")
        : HttpResponse.json({}),
    ),
  );
  await api.get("/api/v1/organization");
}

const banner = () => screen.queryByRole("alert");

describe("suscripción suspendida", () => {
  it("la cuenta entra sin /users/me y queda confinada en /consumo con el banner", async () => {
    const { cuenta } = backendConSuscripcion({ suspendida: true });
    await entrarDesde("/tickets");

    await waitFor(() => expect(rutaActual()).toBe("/consumo"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/suscripción .* está suspendida/);
    expect(screen.queryByRole("link", { name: /Tickets/ })).not.toBeInTheDocument();
    expect(cuenta.perfiles).toBe(0);
  });

  it("no consulta notificaciones mientras está suspendida", async () => {
    const { cuenta } = backendConSuscripcion({ suspendida: true });
    await entrarDesde("/consumo");
    await screen.findByRole("heading", { name: "Consumo y plan" });

    expect(cuenta.notificaciones).toBe(0);
  });

  it("el banner sale en el idioma del usuario", async () => {
    localStorage.setItem("kc_idioma", "pt");
    backendConSuscripcion({ suspendida: true });
    await entrarDesde();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /A assinatura da sua organização está suspensa/,
    );
  });

  it("si se reactiva, la siguiente renovación levanta el confinamiento y verifica el perfil", async () => {
    const { estado, cuenta } = backendConSuscripcion({ suspendida: true });
    await entrarDesde("/consumo");
    await screen.findByRole("alert");

    estado.suspendida = false;
    await renovar();

    await waitFor(() => expect(banner()).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Tickets/ })).toBeInTheDocument();
    await waitFor(() => expect(cuenta.perfiles).toBe(1));
  });

  it("si se suspende durante la sesión, la siguiente renovación la confina", async () => {
    const { estado, cuenta } = backendConSuscripcion({ suspendida: false });
    await entrarDesde("/tickets");
    await waitFor(() => expect(rutaActual()).toBe("/tickets"));
    expect(cuenta.perfiles).toBe(1);

    estado.suspendida = true;
    await renovar();

    await waitFor(() => expect(rutaActual()).toBe("/consumo"));
    expect(banner()).toBeInTheDocument();
    // No se pide /users/me: el backend lo rechazaría.
    expect(cuenta.perfiles).toBe(1);
  });
});
