import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { alExpirarSesion, api, ApiError, fijarSesion, haySesion } from "./client";

describe("cliente de la API", () => {
  it("adjunta Bearer y X-Trace-Id en cada petición", async () => {
    const cabeceras = vi.fn();
    server.use(
      http.get("*/api/v1/organization", ({ request }) => {
        cabeceras(request.headers.get("Authorization"), request.headers.get("X-Trace-Id"));
        return HttpResponse.json({});
      }),
    );
    fijarSesion("acc_1", "ref_1");

    await api.get("/api/v1/organization");

    expect(cabeceras).toHaveBeenCalledWith("Bearer acc_1", expect.stringMatching(/^trw_/));
  });

  it("convierte el sobre de error del backend en ApiError con trace_id", async () => {
    server.use(
      http.get("*/api/v1/tickets/:numero", () =>
        sobreError(404, "NO_ENCONTRADO", "El recurso no existe."),
      ),
    );
    fijarSesion("acc_1", "ref_1");

    const error = await api.get("/api/v1/tickets/KC-9998").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      http: 404,
      codigo: "NO_ENCONTRADO",
      message: "El recurso no existe.",
      traceId: "tr_msw_prueba",
    });
  });

  it("ante 401 renueva el token una vez y reintenta con el nuevo", async () => {
    const tokensVistos: (string | null)[] = [];
    server.use(
      http.get("*/api/v1/organization", ({ request }) => {
        const auth = request.headers.get("Authorization");
        tokensVistos.push(auth);
        return auth === "Bearer acc_1"
          ? sobreError(401, "NO_AUTENTICADO", "El token ha expirado.")
          : HttpResponse.json({ id: "org_demo" });
      }),
    );
    fijarSesion("acc_1", "ref_1");

    const org = await api.get<{ id: string }>("/api/v1/organization");

    expect(org.id).toBe("org_demo");
    expect(tokensVistos).toEqual(["Bearer acc_1", "Bearer acc_2"]);
    expect(sessionStorage.getItem("kc_refresh")).toBe("ref_2");
  });

  it("si la renovación falla, cierra la sesión y avisa", async () => {
    server.use(
      http.get("*/api/v1/organization", () => sobreError(401, "NO_AUTENTICADO", "Expirado.")),
      http.post("*/api/v1/auth/refresh", () => sobreError(401, "AUTH_REFRESH_INVALIDO", "No.")),
    );
    const alExpirar = vi.fn();
    alExpirarSesion(alExpirar);
    fijarSesion("acc_1", "ref_1");

    await expect(api.get("/api/v1/organization")).rejects.toMatchObject({ http: 401 });

    expect(alExpirar).toHaveBeenCalledOnce();
    expect(haySesion()).toBe(false);
  });

  it("no intenta renovar en las rutas de /auth/", async () => {
    const refresh = vi.fn();
    server.use(
      http.post("*/api/v1/auth/refresh", () => {
        refresh();
        return HttpResponse.json({});
      }),
    );

    await expect(
      api.post("/api/v1/auth/login", { email: "x@y.z", password: "mala" }),
    ).rejects.toMatchObject({ http: 401, codigo: "AUTH_CREDENCIALES_INVALIDAS" });
    expect(refresh).not.toHaveBeenCalled();
  });

  it("devuelve undefined en respuestas 204", async () => {
    server.use(
      http.patch("*/api/v1/tickets/:numero", () => new HttpResponse(null, { status: 204 })),
    );
    fijarSesion("acc_1", "ref_1");

    await expect(api.patch("/api/v1/tickets/KC-1001", {})).resolves.toBeUndefined();
  });
});
