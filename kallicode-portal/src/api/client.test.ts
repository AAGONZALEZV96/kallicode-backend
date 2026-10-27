import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import {
  alExpirarSesion,
  api,
  ApiError,
  cerrarSesion,
  fijarSesion,
  haySesion,
  limpiarSesion,
  MARGEN_RENOVACION_MS,
  MAX_REINTENTOS_429,
  TIEMPO_LIMITE_MS,
} from "./client";

/** Refresh de un solo uso, como el backend: reutilizarlo es replay y se rechaza. */
function refreshDeUnSoloUso(antesDeResponder?: () => Promise<void>) {
  const usados = new Set<string>();
  const estado = { llamadas: 0 };
  const handler = http.post("*/api/v1/auth/refresh", async ({ request }) => {
    estado.llamadas++;
    const { refresh_token } = (await request.json()) as { refresh_token: string };
    if (usados.has(refresh_token)) {
      return sobreError(401, "AUTH_REFRESH_REUTILIZADO", "Se cerraron todas tus sesiones.");
    }
    usados.add(refresh_token);
    await antesDeResponder?.();
    const n = usados.size + 1;
    return HttpResponse.json({ access_token: `acc_${n}`, refresh_token: `ref_${n}` });
  });
  return { estado, handler };
}

/** Responde 401 al access token inicial y OK a cualquier otro. */
const panelConAcc1Vencido = http.get("*/api/v1/dashboard/:panel", ({ request, params }) =>
  request.headers.get("Authorization") === "Bearer acc_1"
    ? sobreError(401, "NO_AUTENTICADO", "El token ha expirado.")
    : HttpResponse.json({ panel: params.panel }),
);

function jwtQueVenceEn(ms: number): string {
  const b64url = (o: object) =>
    btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  const exp = Math.floor((Date.now() + ms) / 1000);
  return `${b64url({ alg: "RS256", typ: "JWT" })}.${b64url({ exp })}.firma`;
}

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

describe("renovación single-flight (PT-F01)", () => {
  it("varias peticiones con 401 a la vez comparten una sola renovación", async () => {
    const refresh = refreshDeUnSoloUso();
    server.use(refresh.handler, panelConAcc1Vencido);
    fijarSesion("acc_1", "ref_1");

    // Como el Tablero: tres peticiones en paralelo con el token vencido.
    const respuestas = await Promise.all([
      api.get("/api/v1/dashboard/summary"),
      api.get("/api/v1/dashboard/lines"),
      api.get("/api/v1/dashboard/kanban"),
    ]);

    expect(respuestas).toEqual([{ panel: "summary" }, { panel: "lines" }, { panel: "kanban" }]);
    expect(refresh.estado.llamadas).toBe(1);
    expect(haySesion()).toBe(true);
  });

  it("un 401 que llega después de renovar reintenta con el token nuevo sin renovar otra vez", async () => {
    const refresh = refreshDeUnSoloUso();
    let renovado = () => {};
    const tokenNuevoEnUso = new Promise<void>((r) => (renovado = r));
    server.use(
      refresh.handler,
      http.get("*/api/v1/dashboard/summary", ({ request }) => {
        if (request.headers.get("Authorization") === "Bearer acc_1") {
          return sobreError(401, "NO_AUTENTICADO", "El token ha expirado.");
        }
        renovado();
        return HttpResponse.json({ panel: "summary" });
      }),
      // Petición lenta: su 401 llega cuando la otra ya renovó.
      http.get("*/api/v1/dashboard/lines", async ({ request }) => {
        if (request.headers.get("Authorization") !== "Bearer acc_1") {
          return HttpResponse.json({ panel: "lines" });
        }
        await tokenNuevoEnUso;
        return sobreError(401, "NO_AUTENTICADO", "El token ha expirado.");
      }),
    );
    fijarSesion("acc_1", "ref_1");

    await Promise.all([api.get("/api/v1/dashboard/summary"), api.get("/api/v1/dashboard/lines")]);

    expect(refresh.estado.llamadas).toBe(1);
  });

  it("si la sesión se cierra durante la renovación, no la revive", async () => {
    let responder: (() => void) | undefined;
    const refresh = refreshDeUnSoloUso(() => new Promise<void>((r) => (responder = r)));
    server.use(refresh.handler, panelConAcc1Vencido);
    fijarSesion("acc_1", "ref_1");

    const peticion = api.get("/api/v1/dashboard/summary");
    await vi.waitFor(() => expect(responder).toBeDefined());
    limpiarSesion(); // el usuario pulsa "Salir"
    responder?.();

    await expect(peticion).rejects.toMatchObject({ http: 401 });
    expect(haySesion()).toBe(false);
  });
});

describe("cierre de sesión en el servidor (PT-F03)", () => {
  /** Registra cada POST /auth/logout como [Authorization, refresh_token]. */
  function logoutQueRegistra(responder: (auth: string | null) => Response | Promise<Response>) {
    const llamadas: [string | null, string][] = [];
    const handler = http.post("*/api/v1/auth/logout", async ({ request }) => {
      const auth = request.headers.get("Authorization");
      const { refresh_token } = (await request.json()) as { refresh_token: string };
      llamadas.push([auth, refresh_token]);
      return responder(auth);
    });
    return { llamadas, handler };
  }

  it("revoca la sesión con el access en la cabecera y el refresh en el cuerpo", async () => {
    const logout = logoutQueRegistra(() => HttpResponse.json({ cerradas: 1 }));
    server.use(logout.handler);
    fijarSesion("acc_1", "ref_1");

    await cerrarSesion();

    expect(logout.llamadas).toEqual([["Bearer acc_1", "ref_1"]]);
    expect(haySesion()).toBe(false);
  });

  it("si el access venció, renueva para poder revocar el refresh nuevo", async () => {
    const refresh = refreshDeUnSoloUso();
    const logout = logoutQueRegistra((auth) =>
      auth === "Bearer acc_1"
        ? sobreError(401, "NO_AUTENTICADO", "El token ha expirado.")
        : HttpResponse.json({ cerradas: 1 }),
    );
    server.use(refresh.handler, logout.handler);
    fijarSesion("acc_1", "ref_1");

    await cerrarSesion();

    expect(logout.llamadas).toEqual([
      ["Bearer acc_1", "ref_1"],
      ["Bearer acc_2", "ref_2"],
    ]);
    expect(haySesion()).toBe(false);
  });

  it("con una renovación en curso, espera y revoca el refresh que esta deja vivo", async () => {
    let responder: (() => void) | undefined;
    const refresh = refreshDeUnSoloUso(() => new Promise<void>((r) => (responder = r)));
    const logout = logoutQueRegistra(() => HttpResponse.json({ cerradas: 1 }));
    server.use(refresh.handler, logout.handler, panelConAcc1Vencido);
    fijarSesion("acc_1", "ref_1");

    const peticion = api.get("/api/v1/dashboard/summary").catch(() => undefined);
    await vi.waitFor(() => expect(responder).toBeDefined());
    const cierre = cerrarSesion(); // el usuario pulsa "Salir" a mitad de la renovación
    responder?.();
    await Promise.all([cierre, peticion]);

    expect(logout.llamadas).toEqual([["Bearer acc_2", "ref_2"]]);
    expect(haySesion()).toBe(false);
  });

  it("sin red igual cierra la sesión local", async () => {
    server.use(http.post("*/api/v1/auth/logout", () => HttpResponse.error()));
    fijarSesion("acc_1", "ref_1");

    await expect(cerrarSesion()).resolves.toBeUndefined();
    expect(haySesion()).toBe(false);
  });

  it("un backend lento no borra una sesión nueva iniciada mientras tanto", async () => {
    let liberar = () => {};
    const respuestaRetenida = new Promise<void>((r) => (liberar = r));
    const logout = logoutQueRegistra(async () => {
      await respuestaRetenida;
      return HttpResponse.json({ cerradas: 1 });
    });
    server.use(logout.handler);
    fijarSesion("acc_1", "ref_1");

    const cierre = cerrarSesion();
    await vi.waitFor(() => expect(logout.llamadas).toHaveLength(1));
    expect(haySesion()).toBe(false); // fuera de inmediato, sin esperar al backend

    fijarSesion("acc_9", "ref_9"); // vuelve a entrar antes de que responda el logout
    liberar();
    await cierre;

    expect(haySesion()).toBe(true);
  });
});

describe("custodia de credenciales (PT-F02)", () => {
  it("no deja tokens en sessionStorage ni en localStorage, tampoco tras renovar", async () => {
    server.use(panelConAcc1Vencido);
    fijarSesion("acc_1", "ref_1");

    // El 401 fuerza una renovación: los tokens nuevos tampoco deben persistirse.
    await api.get("/api/v1/dashboard/summary");

    expect(haySesion()).toBe(true);
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });
});

describe("errores de transporte y 429 (PT-F14)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
    fijarSesion("acc_1", "ref_1");
  });
  afterEach(() => vi.useRealTimers());

  /** 429 como el backend: sobre de error + cabecera Retry-After. */
  function limite(retryAfter: string) {
    return HttpResponse.json(
      { error: { codigo: "RATE_LIMIT_EXCEDIDO", mensaje: "Demasiadas peticiones." } },
      { status: 429, headers: { "Retry-After": retryAfter } },
    );
  }

  it("corta la petición al llegar al tiempo límite", async () => {
    let liberar = () => {};
    server.use(
      http.get("*/api/v1/organization", async () => {
        await new Promise<void>((r) => (liberar = r));
        return HttpResponse.json({});
      }),
    );

    const peticion = api.get("/api/v1/organization").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(TIEMPO_LIMITE_MS);

    expect(await peticion).toMatchObject({ http: 0, codigo: "TIEMPO_AGOTADO" });
    liberar();
  });

  it("sin red responde con un ApiError SIN_CONEXION en vez de un TypeError", async () => {
    server.use(http.get("*/api/v1/organization", () => HttpResponse.error()));

    const error = await api.get("/api/v1/organization").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ http: 0, codigo: "SIN_CONEXION" });
  });

  // TanStack Query cancela así las consultas que ya no se necesitan (KC-PT-04-1).
  it("si quien llama cancela, rechaza con AbortError y no como un fallo de red", async () => {
    let liberar = () => {};
    server.use(
      http.get("*/api/v1/organization", async () => {
        await new Promise<void>((r) => (liberar = r));
        return HttpResponse.json({});
      }),
    );
    const control = new AbortController();

    const peticion = api.get("/api/v1/organization", { signal: control.signal }).catch((e) => e);
    control.abort();
    const error = await peticion;

    expect(error).not.toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ name: "AbortError" });
    liberar();
  });

  it("con la señal ya cancelada no llega a enviar la petición", async () => {
    let llamadas = 0;
    server.use(
      http.get("*/api/v1/organization", () => {
        llamadas++;
        return HttpResponse.json({});
      }),
    );

    const error = await api
      .get("/api/v1/organization", { signal: AbortSignal.abort() })
      .catch((e) => e);

    expect(error).toMatchObject({ name: "AbortError" });
    expect(llamadas).toBe(0);
  });

  it("ante un 429 corto espera lo que pide Retry-After y reintenta", async () => {
    let llamadas = 0;
    server.use(
      http.get("*/api/v1/organization", () =>
        ++llamadas === 1 ? limite("2") : HttpResponse.json({ id: "org_demo" }),
      ),
    );

    const peticion = api.get<{ id: string }>("/api/v1/organization");
    await vi.advanceTimersByTimeAsync(1_900);
    expect(llamadas).toBe(1); // aún no pasan los 2 s

    await vi.advanceTimersByTimeAsync(200);
    await expect(peticion).resolves.toEqual({ id: "org_demo" });
    expect(llamadas).toBe(2);
  });

  it("entiende Retry-After como fecha HTTP", async () => {
    let llamadas = 0;
    server.use(
      http.get("*/api/v1/organization", () =>
        ++llamadas === 1
          ? limite(new Date(Date.now() + 2_000).toUTCString())
          : HttpResponse.json({ id: "org_demo" }),
      ),
    );

    const peticion = api.get("/api/v1/organization");
    await vi.advanceTimersByTimeAsync(3_000);

    await expect(peticion).resolves.toEqual({ id: "org_demo" });
    expect(llamadas).toBe(2);
  });

  it("con un Retry-After largo no deja la pantalla esperando: devuelve el 429", async () => {
    let llamadas = 0;
    server.use(
      http.get("*/api/v1/organization", () => {
        llamadas++;
        return limite("60");
      }),
    );

    await expect(api.get("/api/v1/organization")).rejects.toMatchObject({
      http: 429,
      codigo: "RATE_LIMIT_EXCEDIDO",
    });
    expect(llamadas).toBe(1);
  });

  it("deja de reintentar tras el máximo de reintentos", async () => {
    let llamadas = 0;
    server.use(
      http.get("*/api/v1/organization", () => {
        llamadas++;
        return limite("1");
      }),
    );

    const peticion = api.get("/api/v1/organization").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5_000);

    expect(await peticion).toMatchObject({ http: 429 });
    expect(llamadas).toBe(1 + MAX_REINTENTOS_429);
  });
});

describe("renovación proactiva", () => {
  beforeEach(() =>
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] }),
  );
  afterEach(() => vi.useRealTimers());

  it("renueva el token antes de su exp, sin esperar a un 401", async () => {
    const refresh = refreshDeUnSoloUso();
    server.use(refresh.handler);
    const vigencia = 10 * 60_000;
    fijarSesion(jwtQueVenceEn(vigencia), "ref_1");

    await vi.advanceTimersByTimeAsync(vigencia - MARGEN_RENOVACION_MS - 5_000);
    expect(refresh.estado.llamadas).toBe(0);

    await vi.advanceTimersByTimeAsync(10_000);
    await vi.waitFor(() => expect(refresh.estado.llamadas).toBe(1));

    // La siguiente petición ya sale con el token renovado.
    let auth: string | null = null;
    server.use(
      http.get("*/api/v1/organization", ({ request }) => {
        auth = request.headers.get("Authorization");
        return HttpResponse.json({});
      }),
    );
    await api.get("/api/v1/organization");
    expect(auth).toBe("Bearer acc_2");
  });

  it("al cerrar sesión cancela la renovación programada", async () => {
    const refresh = refreshDeUnSoloUso();
    server.use(refresh.handler);
    fijarSesion(jwtQueVenceEn(10 * 60_000), "ref_1");

    limpiarSesion();
    await vi.advanceTimersByTimeAsync(10 * 60_000);

    expect(refresh.estado.llamadas).toBe(0);
  });
});
