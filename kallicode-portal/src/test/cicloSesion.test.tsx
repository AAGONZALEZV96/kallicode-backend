/**
 * Ciclo de sesión de punta a punta con el reloj simulado (KC-PT-02-6).
 *
 * El portal completo (login, Tablero y polling de la barra) contra un backend de sesión
 * simulado con tokens que vencen, refresh de un solo uso y revocaciones. Cada prueba
 * hace pasar el tiempo y comprueba lo que verían el usuario y el servidor.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { crearBackendSesion, TTL_ACCESS_MS } from "../mocks/backendSesion";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "./portal";

const MINUTO = 60_000;
const HORA = 60 * MINUTO;

let backend: ReturnType<typeof crearBackendSesion>;

beforeEach(() => {
  vi.useFakeTimers({
    shouldAdvanceTime: true,
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
  });
  backend = crearBackendSesion();
  server.use(...backend.handlers);
});
afterEach(() => vi.useRealTimers());

const conReloj = { advanceTimers: vi.advanceTimersByTime };

async function entrarAlTablero() {
  const usuario = await entrarDesde("/", conReloj);
  await screen.findByRole("heading", { name: "Tablero de avance" });
  return usuario;
}

describe("ciclo de sesión con el reloj simulado", () => {
  it("una sesión abierta 1 hora se mantiene sola: renueva antes del exp, sin 401 ni replay", async () => {
    await entrarAlTablero();

    // Mientras tanto, el Tablero refresca cada 60 s y la barra cada 30 s.
    await vi.advanceTimersByTimeAsync(HORA);

    // Access de 30 min renovado 1 min antes: a los 29 y a los 58 minutos.
    expect(backend.cuenta.renovaciones).toBe(2);
    expect(backend.cuenta.rechazos401).toBe(0);
    expect(backend.cuenta.replays).toBe(0);
    // El perfil se verifica al entrar y tras cada renovación.
    expect(backend.cuenta.perfiles).toBe(3);
    expect(screen.getByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
  }, 20_000);

  it("si el servidor invalida el access, las peticiones simultáneas renuevan una sola vez", async () => {
    // Refresh lento (2 s): lo que llegue mientras tanto coincide con la renovación en curso.
    backend = crearBackendSesion({ demoraRefreshMs: 2_000 });
    server.use(...backend.handlers);
    await entrarAlTablero();
    // Justo después del polling de los 30 s: el siguiente choque es a los 60 s, cuando
    // coinciden el refresco del Tablero (3 peticiones) y el de la barra.
    await vi.advanceTimersByTimeAsync(31_000);
    backend.invalidarAccessVigentes();

    // Hasta los 60 s del choque más los 2 s del refresh: la renovación termina dentro de
    // la prueba (si quedara pendiente, la siguiente prueba la heredaría).
    await vi.advanceTimersByTimeAsync(35_000);
    await waitFor(() => expect(backend.cuenta.perfiles).toBe(2)); // perfil verificado tras renovar
    expect(backend.cuenta.renovaciones).toBe(1);

    expect(backend.cuenta.rechazos401).toBeGreaterThan(1); // varias a la vez recibieron 401
    expect(backend.cuenta.replays).toBe(0);
    expect(screen.queryByTestId("tablero.refresco.aviso")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tablero de avance" })).toBeInTheDocument();
  });

  it("si cierran todas las sesiones desde otro dispositivo, vuelve a /login conservando la página", async () => {
    await entrarDesde("/aprobaciones", conReloj);
    await screen.findByRole("heading", { name: "Aprobaciones pendientes" });

    backend.cerrarTodasLasSesiones();
    await vi.advanceTimersByTimeAsync(30_000); // siguiente polling de la barra

    await waitFor(() => expect(rutaActual()).toBe("/login?siguiente=%2Faprobaciones"));
    expect(screen.getByRole("button", { name: "Entrar" })).toBeInTheDocument();
    expect(backend.cuenta.replays).toBe(0);
  });

  it("un cambio de perfil hecho por un admin llega a la interfaz en la siguiente renovación", async () => {
    await entrarAlTablero();
    expect(screen.getByTitle("Olga Owner")).toBeInTheDocument();

    backend.cambiarPerfil({ nombre: "Olga Directora" });
    await vi.advanceTimersByTimeAsync(TTL_ACCESS_MS - MINUTO);

    expect(await screen.findByTitle("Olga Directora")).toBeInTheDocument();
    expect(backend.cuenta.renovaciones).toBe(1);
  });

  it("tras «Salir» el refresh queda revocado y no sale ninguna petición más", async () => {
    await entrarAlTablero();
    const usuario = userEvent.setup(conReloj);

    await usuario.click(screen.getByRole("button", { name: "Salir" }));
    await screen.findByRole("button", { name: "Entrar" });
    await waitFor(() => expect(backend.refreshRevocado("kcrt_1")).toBe(true));

    const antes = { ...backend.cuenta };
    await vi.advanceTimersByTimeAsync(HORA);

    expect(backend.cuenta.peticiones).toBe(antes.peticiones);
    expect(backend.cuenta.renovaciones).toBe(antes.renovaciones);
  });
});
