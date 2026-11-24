/**
 * Equipo (KC-PT-10-5), en el portal completo: la lista con filtros en la URL, invitar y cambiar
 * el rol o el estado según las reglas del backend.
 */
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../api/tipos";
import { perfilOwner } from "../mocks/datos";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { entrarDesde, rutaActual } from "../test/portal";

const id = (testid: string) => screen.getByTestId(testid);
const conRol = (rol: Rol) =>
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));

/** La fila de una persona por su nombre. */
const fila = (nombre: string) =>
  screen.getAllByTestId("equipo.lista.fila").find((f) => f.textContent?.includes(nombre))!;

/** Peticiones a /users: método, ruta con consulta y cuerpo. */
function pedidas() {
  const lista: { metodo: string; ruta: string; cuerpo?: unknown }[] = [];
  server.events.on("request:start", async ({ request }) => {
    const u = new URL(request.url);
    if (!u.pathname.startsWith("/api/v1/users") || u.pathname === "/api/v1/users/me") return;
    const cuerpo = request.method === "GET" ? undefined : await request.clone().json();
    lista.push({ metodo: request.method, ruta: u.pathname + u.search, cuerpo });
  });
  return lista;
}

async function abrir(ruta = "/equipo") {
  const usuario = await entrarDesde(ruta);
  await screen.findAllByTestId("equipo.lista.fila");
  return usuario;
}

describe("la lista", () => {
  it("desde el menú: todas las personas, la propia marcada y sin controles", async () => {
    const usuario = await entrarDesde("/");
    await usuario.click(await screen.findByTestId("layout.nav.equipo"));
    await screen.findAllByTestId("equipo.lista.fila");

    expect(screen.getAllByTestId("equipo.lista.fila")).toHaveLength(6);
    const propia = fila("Olga Owner");
    expect(propia).toHaveTextContent("tú");
    expect(within(propia).queryByRole("combobox")).toBeNull();
    expect(within(propia).queryByRole("button")).toBeNull();
    // El owner gestiona al resto, incluido el admin.
    expect(within(fila("Ana Admin")).getByTestId("equipo.lista.rol")).toHaveValue("admin");
    expect(within(fila("María G.")).getByTestId("equipo.lista.estado")).toHaveTextContent(
      "Desactivar",
    );
    // A un invitado no se lo activa desde aquí.
    expect(within(fila("Iván Invitado")).queryByTestId("equipo.lista.estado")).toBeNull();
    expect(fila("Iván Invitado")).toHaveTextContent("Invitado");
    expect(id("equipo.lista.tabla")).toHaveTextContent("Nunca");
  });

  it("un admin no gestiona al owner ni a otro admin, y no ofrece esos roles", async () => {
    conRol("admin");
    await abrir();

    expect(within(fila("Ana Admin")).queryByTestId("equipo.lista.rol")).toBeNull();
    expect(within(fila("Ana Admin")).queryByTestId("equipo.lista.estado")).toBeNull();
    const rol = within(fila("María G.")).getByTestId("equipo.lista.rol");
    expect([...rol.querySelectorAll("option")].map((o) => o.value)).toEqual([
      "architect",
      "approver",
      "member",
      "viewer",
    ]);
    expect(id("equipo.cabecera.invitar")).toBeInTheDocument();
  });

  it.each<Rol>(["architect", "approver"])("un %s ve la lista sin gestionarla", async (rol) => {
    conRol(rol);
    await abrir();

    expect(screen.queryByTestId("equipo.cabecera.invitar")).toBeNull();
    expect(screen.queryAllByTestId("equipo.lista.rol")).toHaveLength(0);
    expect(screen.queryAllByTestId("equipo.lista.estado")).toHaveLength(0);
  });

  it("un member no la ve en el menú ni puede abrirla", async () => {
    conRol("member");
    await entrarDesde("/equipo");

    await waitFor(() => expect(rutaActual()).not.toBe("/equipo"));
    expect(screen.queryByTestId("layout.nav.equipo")).toBeNull();
  });

  it("los filtros van a la URL y a GET /users, y vuelven a la página 1", async () => {
    const enviadas = pedidas();
    const usuario = await abrir("/equipo?page=2");

    await usuario.selectOptions(id("equipo.filtros.rol"), "member");
    await waitFor(() => expect(rutaActual()).toBe("/equipo?rol=member"));
    await usuario.selectOptions(id("equipo.filtros.estado"), "activo");
    await usuario.type(id("equipo.filtros.busqueda"), "maría");

    await waitFor(() =>
      expect(rutaActual()).toBe(
        `/equipo?rol=member&estado=activo&q=${encodeURIComponent("maría")}`,
      ),
    );
    await waitFor(() =>
      expect(enviadas.at(-1)?.ruta).toBe(
        `/api/v1/users?page=1&page_size=25&q=${encodeURIComponent("maría")}&rol=member&estado=activo`,
      ),
    );
    expect(screen.getAllByTestId("equipo.lista.fila")).toHaveLength(1);
  });

  it("un rol desconocido en la URL se ignora", async () => {
    const enviadas = pedidas();
    await abrir("/equipo?rol=superusuario");

    expect(id("equipo.filtros.rol")).toHaveValue("");
    expect(enviadas.every((p) => !p.ruta.includes("rol="))).toBe(true);
  });

  it("sin resultados con filtros, lo dice", async () => {
    const usuario = await abrir();
    await usuario.type(id("equipo.filtros.busqueda"), "zzz");

    expect(await screen.findByTestId("equipo.lista.vacio")).toHaveTextContent(
      "Nadie coincide con estos filtros",
    );
  });
});

describe("invitar", () => {
  it("con un correo válido: rol Miembro por defecto, mensaje y aviso con el vencimiento", async () => {
    const enviadas = pedidas();
    const usuario = await abrir();
    await usuario.click(id("equipo.cabecera.invitar"));
    const dialogo = await screen.findByTestId("equipo.invitar.dialogo");

    expect(within(dialogo).getByTestId("equipo.invitar.rol")).toHaveValue("member");
    expect(dialogo).toHaveTextContent("Crea tickets y sigue su avance.");
    await usuario.type(within(dialogo).getByTestId("equipo.invitar.correo"), " Nueva@Demo.cl ");
    await usuario.type(within(dialogo).getByTestId("equipo.invitar.mensaje"), "Bienvenida");
    await usuario.click(within(dialogo).getByTestId("equipo.invitar.enviar"));

    expect(await screen.findByTestId("equipo.avisos.mensaje")).toHaveTextContent(
      /Invitación enviada a nueva@demo\.cl\. Vence el 1 dic 2026/,
    );
    expect(screen.queryByTestId("equipo.invitar.dialogo")).toBeNull();
    expect(enviadas.filter((p) => p.metodo === "POST").map((p) => p.cuerpo)).toEqual([
      { email: "Nueva@Demo.cl", rol: "member", mensaje: "Bienvenida" },
    ]);
  });

  it("un correo inválido se marca sin enviar", async () => {
    const enviadas = pedidas();
    const usuario = await abrir();
    await usuario.click(id("equipo.cabecera.invitar"));
    await usuario.type(await screen.findByTestId("equipo.invitar.correo"), "no-es-correo");
    await usuario.click(id("equipo.invitar.enviar"));

    expect(id("equipo.invitar.error-correo")).toHaveTextContent("Escribe un correo válido.");
    expect(id("equipo.invitar.correo")).toHaveFocus();
    expect(enviadas.filter((p) => p.metodo === "POST")).toEqual([]);
  });

  it("una invitación duplicada: el error traducido queda en el diálogo", async () => {
    const usuario = await abrir();
    await usuario.click(id("equipo.cabecera.invitar"));
    await usuario.type(
      await screen.findByTestId("equipo.invitar.correo"),
      "pendiente@demo.kallicode.dev",
    );
    await usuario.click(id("equipo.invitar.enviar"));

    expect(await within(id("equipo.invitar.dialogo")).findByRole("alert")).toHaveTextContent(
      "Ya hay una invitación pendiente para este correo.",
    );
  });

  it("el admin solo invita roles sin administración", async () => {
    conRol("admin");
    const usuario = await abrir();
    await usuario.click(id("equipo.cabecera.invitar"));

    const opciones = [
      ...(await screen.findByTestId("equipo.invitar.rol")).querySelectorAll("option"),
    ];
    expect(opciones.map((o) => o.value)).toEqual(["architect", "approver", "member", "viewer"]);
  });
});

describe("cambiar el rol o el estado", () => {
  it("elegir otro rol pide confirmación, lo guarda y vuelve a pedir la lista", async () => {
    const enviadas = pedidas();
    const usuario = await abrir();

    await usuario.selectOptions(
      within(fila("María G.")).getByTestId("equipo.lista.rol"),
      "approver",
    );
    const dialogo = await screen.findByTestId("equipo.cambio.dialogo");
    expect(dialogo).toHaveTextContent("María G. pasará de Miembro a Aprobador.");
    const antes = enviadas.filter((p) => p.metodo === "GET").length;
    await usuario.click(within(dialogo).getByTestId("equipo.cambio.confirmar"));

    expect(await screen.findByTestId("equipo.avisos.mensaje")).toHaveTextContent(
      "María G. ahora es Aprobador.",
    );
    expect(enviadas.filter((p) => p.metodo === "PATCH")).toEqual([
      { metodo: "PATCH", ruta: "/api/v1/users/u_dev", cuerpo: { rol: "approver" } },
    ]);
    await waitFor(() =>
      expect(enviadas.filter((p) => p.metodo === "GET").length).toBeGreaterThan(antes),
    );
  });

  it("cancelar no cambia nada", async () => {
    const enviadas = pedidas();
    const usuario = await abrir();
    await usuario.selectOptions(within(fila("María G.")).getByTestId("equipo.lista.rol"), "viewer");
    await usuario.click(await screen.findByTestId("equipo.cambio.cancelar"));

    expect(screen.queryByTestId("equipo.cambio.dialogo")).toBeNull();
    expect(within(fila("María G.")).getByTestId("equipo.lista.rol")).toHaveValue("member");
    expect(enviadas.filter((p) => p.metodo === "PATCH")).toEqual([]);
  });

  it("desactivar explica que cierra sus sesiones y lo guarda", async () => {
    const enviadas = pedidas();
    const usuario = await abrir();
    await usuario.click(within(fila("María G.")).getByTestId("equipo.lista.estado"));

    const dialogo = await screen.findByTestId("equipo.cambio.dialogo");
    expect(dialogo).toHaveTextContent("se cerrarán sus sesiones abiertas");
    await usuario.click(within(dialogo).getByTestId("equipo.cambio.confirmar"));

    await screen.findByTestId("equipo.avisos.mensaje");
    expect(enviadas.filter((p) => p.metodo === "PATCH").map((p) => p.cuerpo)).toEqual([
      { estado: "desactivado" },
    ]);
  });

  it("el único arquitecto: el error traducido queda en el diálogo", async () => {
    server.use(
      http.patch("*/api/v1/users/:id", () =>
        sobreError(409, "ULTIMO_ARQUITECTO", "No puedes quitar el rol."),
      ),
    );
    const usuario = await abrir();
    await usuario.click(within(fila("Max K.")).getByTestId("equipo.lista.estado"));
    await usuario.click(await screen.findByTestId("equipo.cambio.confirmar"));

    expect(await within(id("equipo.cambio.dialogo")).findByRole("alert")).toHaveTextContent(
      "Es el único arquitecto activo: nadie más podría firmar el Gate 3.",
    );
    expect(screen.queryByTestId("equipo.avisos.mensaje")).toBeNull();
  });
});
