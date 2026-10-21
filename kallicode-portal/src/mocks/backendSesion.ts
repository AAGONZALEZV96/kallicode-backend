/**
 * Backend de sesión simulado para la suite de ciclo de sesión (KC-PT-02-6).
 *
 * Reproduce las reglas del backend real que importan al portal:
 * - access JWT con `exp` (30 min) e `iat`, validado contra el reloj (simulado en las pruebas);
 * - refresh rotativo de un solo uso: reutilizarlo es replay y revoca todas las sesiones;
 * - logout que revoca el refresh y el `jti` del access;
 * - "sello" por usuario (como `invalidar_usuario`): invalida todo lo emitido antes.
 *
 * Sus handlers van delante de los dobles generales: validan la sesión en cualquier
 * `/api/v1/*` y, si es válida, dejan pasar la petición al doble de cada endpoint.
 */
import { http, HttpResponse } from "msw";

import * as d from "./datos";
import { sobreError } from "./handlers";

export const TTL_ACCESS_MS = 30 * 60_000;
const TTL_REFRESH_MS = 30 * 24 * 60 * 60_000;

interface Refresh {
  usado: boolean;
  revocado: boolean;
  expira: number;
}

function b64url(o: object): string {
  return btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function leerClaims(access: string): { jti: string; iat: number; exp: number } | null {
  try {
    const p = access.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(p));
  } catch {
    return null;
  }
}

/**
 * `demoraRefreshMs` simula un /auth/refresh lento, para que las peticiones que llegan
 * mientras tanto coincidan con la renovación en curso.
 */
export function crearBackendSesion({ demoraRefreshMs = 0 } = {}) {
  const refresh = new Map<string, Refresh>();
  const jtiRevocados = new Set<string>();
  let sello = 0; // ms: todo lo emitido hasta este instante queda invalidado
  let secuencia = 0;
  let perfil = { ...d.perfilOwner };
  const cuenta = {
    logins: 0,
    renovaciones: 0,
    replays: 0,
    rechazos401: 0,
    perfiles: 0,
    /** Peticiones a /api/v1 fuera de /auth/ (vistas, polling, perfil). */
    peticiones: 0,
  };

  function emitirPar() {
    secuencia++;
    const ahora = Date.now();
    const access = [
      b64url({ alg: "RS256", typ: "JWT" }),
      b64url({
        sub: perfil.id,
        jti: `jti_${secuencia}`,
        iat: Math.floor(ahora / 1000),
        exp: Math.floor((ahora + TTL_ACCESS_MS) / 1000),
      }),
      "firma",
    ].join(".");
    const token = `kcrt_${secuencia}`;
    refresh.set(token, { usado: false, revocado: false, expira: ahora + TTL_REFRESH_MS });
    return { access_token: access, refresh_token: token, acceso_restringido: false };
  }

  /** Como `usuario_actual` + `comprobar_revocacion` del backend. */
  function accessValido(request: Request): { jti: string } | null {
    const bearer = request.headers.get("Authorization")?.replace(/^Bearer /, "");
    const claims = bearer ? leerClaims(bearer) : null;
    if (!claims) return null;
    if (claims.exp * 1000 <= Date.now()) return null;
    if (jtiRevocados.has(claims.jti) || claims.iat * 1000 <= sello) return null;
    return claims;
  }

  function rechazar() {
    cuenta.rechazos401++;
    return sobreError(401, "AUTH_TOKEN_REVOCADO", "La sesión fue cerrada o revocada.");
  }

  const handlers = [
    http.post("*/api/v1/auth/login", () => {
      cuenta.logins++;
      return HttpResponse.json({ ...emitirPar(), usuario: d.usuarioOwner });
    }),
    http.post("*/api/v1/auth/refresh", async ({ request }) => {
      cuenta.renovaciones++;
      const { refresh_token } = (await request.json()) as { refresh_token: string };
      const r = refresh.get(refresh_token);
      if (!r || r.revocado || r.expira <= Date.now()) {
        return sobreError(401, "AUTH_REFRESH_INVALIDO", "La sesión ha expirado.");
      }
      if (r.usado) {
        cuenta.replays++;
        refresh.forEach((x) => (x.revocado = true));
        return sobreError(401, "AUTH_REFRESH_REUTILIZADO", "Se cerraron todas tus sesiones.");
      }
      r.usado = true; // se marca antes de responder: un segundo uso en paralelo es replay
      if (demoraRefreshMs) await new Promise((fin) => setTimeout(fin, demoraRefreshMs));
      return HttpResponse.json(emitirPar());
    }),
    http.post("*/api/v1/auth/logout", async ({ request }) => {
      const claims = accessValido(request);
      if (!claims) return rechazar();
      const { refresh_token } = (await request.json()) as { refresh_token: string };
      const r = refresh.get(refresh_token);
      if (r) r.revocado = true;
      jtiRevocados.add(claims.jti);
      return HttpResponse.json({ cerradas: r ? 1 : 0 });
    }),
    http.get("*/api/v1/users/me", ({ request }) => {
      cuenta.peticiones++;
      if (!accessValido(request)) return rechazar();
      cuenta.perfiles++;
      return HttpResponse.json(perfil);
    }),
    // Resto de /api/v1: valida la sesión y, si es válida, sigue al doble del endpoint.
    http.all("*/api/v1/*", ({ request }) => {
      if (new URL(request.url).pathname.startsWith("/api/v1/auth/")) return;
      cuenta.peticiones++;
      if (!accessValido(request)) return rechazar();
    }),
  ];

  return {
    handlers,
    cuenta,
    /** Otro dispositivo cierra todas las sesiones (o un admin desactiva al usuario). */
    cerrarTodasLasSesiones() {
      sello = Date.now();
      refresh.forEach((x) => (x.revocado = true));
    },
    /** El servidor invalida el access vigente sin tocar el refresh (p. ej. denylist de jti). */
    invalidarAccessVigentes() {
      sello = Date.now();
    },
    /** Un admin cambia datos del usuario: el backend los aplica al instante. */
    cambiarPerfil(cambios: Partial<typeof perfil>) {
      perfil = { ...perfil, ...cambios };
    },
    refreshRevocado(token: string) {
      return refresh.get(token)?.revocado ?? false;
    },
  };
}
