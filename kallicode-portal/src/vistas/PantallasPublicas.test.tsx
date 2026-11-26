/**
 * Pantallas públicas (KC-PT-10-6), en el portal completo y sin sesión: recuperar la contraseña,
 * restablecerla con el código del correo y aceptar una invitación.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "../App";
import { AuthProvider } from "../estado/auth";
import { sobreError } from "../mocks/handlers";
import { server } from "../mocks/server";
import { Proveedores } from "../test/portal";

const CLAVE = "Tq8vLm2Rx9pWz4nB";

function Ruta() {
  const { pathname, search } = useLocation();
  return <output data-testid="prueba.ruta.actual">{pathname + search}</output>;
}
const ruta = () => screen.getByTestId("prueba.ruta.actual").textContent;

/** El portal sin sesión en `inicio`. */
function abrir(inicio: string) {
  const usuario = userEvent.setup();
  render(
    <Proveedores>
      <AuthProvider>
        <MemoryRouter initialEntries={[inicio]}>
          <App />
          <Ruta />
        </MemoryRouter>
      </AuthProvider>
    </Proveedores>,
  );
  return usuario;
}

/** Cuerpos enviados a una ruta de /auth. */
function enviados(sufijo: string) {
  const cuerpos: unknown[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (new URL(request.url).pathname === `/api/v1/auth/${sufijo}`)
      cuerpos.push(await request.clone().json());
  });
  return cuerpos;
}

const id = (testid: string) => screen.getByTestId(testid);

describe("recuperar la contraseña", () => {
  it("desde el login: pide el correo y confirma sin revelar si la cuenta existe", async () => {
    const cuerpos = enviados("forgot-password");
    const usuario = abrir("/login");

    await usuario.click(await screen.findByRole("link", { name: "¿Olvidaste tu contraseña?" }));
    expect(ruta()).toBe("/recuperar-clave");
    // La pantalla se descarga al entrar (KC-PT-11-1): se espera su título.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Recuperar contraseña" }),
    ).toBeInTheDocument();

    await usuario.type(id("recuperar-clave.formulario.correo"), "  ana@demo.kallicode.dev ");
    await usuario.click(id("recuperar-clave.formulario.enviar"));

    const aviso = await screen.findByTestId("recuperar-clave.avisos.enviado");
    expect(aviso).toHaveAttribute("role", "status");
    expect(aviso).toHaveTextContent("Si la cuenta existe, te enviamos un correo con un código");
    expect(cuerpos).toEqual([{ email: "ana@demo.kallicode.dev" }]);

    await usuario.click(id("recuperar-clave.avisos.continuar"));
    expect(ruta()).toBe("/restablecer-clave");
  });

  it("un error del backend (p. ej. demasiadas solicitudes) se muestra y deja reintentar", async () => {
    server.use(
      http.post("*/api/v1/auth/forgot-password", () =>
        sobreError(429, "RATE_LIMIT_EXCEDIDO", "Demasiadas solicitudes."),
      ),
    );
    const usuario = abrir("/recuperar-clave");
    await usuario.type(await screen.findByTestId("recuperar-clave.formulario.correo"), "a@b.cl");
    await usuario.click(id("recuperar-clave.formulario.enviar"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("recuperar-clave.avisos.enviado")).toBeNull();
    expect(id("recuperar-clave.formulario.enviar")).toBeEnabled();
  });
});

describe("restablecer la contraseña", () => {
  async function completar(
    usuario: ReturnType<typeof userEvent.setup>,
    clave = CLAVE,
    otra = clave,
  ) {
    await usuario.type(id("restablecer-clave.formulario.clave"), clave);
    await usuario.type(id("restablecer-clave.formulario.confirmar"), otra);
  }

  it("con el código del enlace: guarda y vuelve al login con el aviso", async () => {
    const cuerpos = enviados("reset-password");
    const usuario = abrir("/restablecer-clave?token=kcpr_valido");

    expect(await screen.findByTestId("restablecer-clave.formulario.codigo")).toHaveValue(
      "kcpr_valido",
    );
    await completar(usuario);
    await usuario.click(id("restablecer-clave.formulario.enviar"));

    await waitFor(() => expect(ruta()).toBe("/login"));
    expect(cuerpos).toEqual([{ token: "kcpr_valido", password_nueva: CLAVE }]);
    expect(await screen.findByTestId("login.avisos.mensaje")).toHaveTextContent(
      "Contraseña actualizada. Inicia sesión con tu nueva contraseña.",
    );
  });

  it("los requisitos se marcan al escribir y describen el campo", async () => {
    const usuario = abrir("/restablecer-clave");
    const campo = await screen.findByTestId("restablecer-clave.formulario.clave");
    const requisitos = id("restablecer-clave.formulario.requisitos");
    const cumplidos = () => requisitos.querySelectorAll("li.cumple").length;

    expect(campo.getAttribute("aria-describedby")).toContain(requisitos.id);
    expect(cumplidos()).toBe(0);
    await usuario.type(campo, "abc");
    expect(cumplidos()).toBe(1);
    expect(within(requisitos).getByText(/Una minúscula/)).toHaveTextContent("(cumplido)");
    await usuario.type(campo, "DEF1234567");
    expect(cumplidos()).toBe(4);
  });

  it.each([
    ["el código sin kcpr_", "abc", CLAVE, CLAVE, "codigo", "El código empieza con kcpr_"],
    [
      "una contraseña sin mayúscula",
      "kcpr_x",
      "minusculas123",
      "minusculas123",
      "clave",
      "no cumple los requisitos",
    ],
    [
      "contraseñas distintas",
      "kcpr_x",
      CLAVE,
      CLAVE + "x",
      "confirmar",
      "Las contraseñas no coinciden.",
    ],
  ])(
    "%s: error junto al campo, foco en él y sin enviar",
    async (_, codigo, clave, otra, campo, texto) => {
      const cuerpos = enviados("reset-password");
      const usuario = abrir("/restablecer-clave");
      await usuario.type(await screen.findByTestId("restablecer-clave.formulario.codigo"), codigo);
      await completar(usuario, clave, otra);
      await usuario.click(id("restablecer-clave.formulario.enviar"));

      expect(id(`restablecer-clave.formulario.error-${campo}`)).toHaveTextContent(texto);
      const control = id(`restablecer-clave.formulario.${campo}`);
      expect(control).toHaveFocus();
      expect(control).toHaveAttribute("aria-invalid", "true");
      expect(cuerpos).toEqual([]);
    },
  );

  it("un código vencido: el error traducido y el enlace para pedir otro", async () => {
    const usuario = abrir("/restablecer-clave?token=kcpr_vencido");
    await screen.findByTestId("restablecer-clave.formulario.codigo");
    await completar(usuario);
    await usuario.click(id("restablecer-clave.formulario.enviar"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "El código expiró o ya se usó. Pide uno nuevo.",
    );
    await usuario.click(id("restablecer-clave.avisos.pedir-otro"));
    expect(ruta()).toBe("/recuperar-clave");
  });

  it("si el backend la rechaza por fácil de adivinar, el error va en el campo", async () => {
    server.use(
      http.post("*/api/v1/auth/reset-password", () =>
        HttpResponse.json(
          {
            error: {
              codigo: "AUTH_PASSWORD_DEBIL",
              mensaje: "Demasiado débil.",
              detalle: { sugerencia: "This is a top-100 common password." },
              trace_id: "tr",
            },
          },
          { status: 422 },
        ),
      ),
    );
    const usuario = abrir("/restablecer-clave?token=kcpr_valido");
    await screen.findByTestId("restablecer-clave.formulario.codigo");
    await completar(usuario);
    await usuario.click(id("restablecer-clave.formulario.enviar"));

    expect(await screen.findByTestId("restablecer-clave.formulario.error-clave")).toHaveTextContent(
      "La contraseña es fácil de adivinar.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
    // Al cambiarla, el error del servidor se va.
    await usuario.type(id("restablecer-clave.formulario.clave"), "x");
    expect(screen.queryByTestId("restablecer-clave.formulario.error-clave")).toBeNull();
  });
});

describe("aceptar una invitación", () => {
  async function completar(usuario: ReturnType<typeof userEvent.setup>) {
    await usuario.type(id("aceptar-invitacion.formulario.nombre"), "  Ana Nueva ");
    await usuario.type(id("aceptar-invitacion.formulario.clave"), CLAVE);
    await usuario.type(id("aceptar-invitacion.formulario.confirmar"), CLAVE);
  }

  it("crea la cuenta en el idioma elegido y entra al portal", async () => {
    const cuerpos = enviados("invitations/accept");
    const usuario = abrir("/invitacion?token=kcin_valido");

    expect(await screen.findByTestId("aceptar-invitacion.formulario.codigo")).toHaveValue(
      "kcin_valido",
    );
    await usuario.selectOptions(id("aceptar-invitacion.formulario.idioma"), "en");
    // La pantalla cambia de idioma al instante.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Join Kallicode");
    await completar(usuario);
    await usuario.click(id("aceptar-invitacion.formulario.enviar"));

    expect(await screen.findByTestId("tablero.vista.raiz")).toBeInTheDocument();
    expect(ruta()).toBe("/");
    expect(cuerpos).toEqual([
      { token: "kcin_valido", nombre: "Ana Nueva", password: CLAVE, idioma: "en" },
    ]);
  });

  it("valida el nombre antes de enviar", async () => {
    const usuario = abrir("/invitacion?token=kcin_valido");
    await usuario.type(await screen.findByTestId("aceptar-invitacion.formulario.nombre"), "A");
    await usuario.click(id("aceptar-invitacion.formulario.enviar"));

    expect(id("aceptar-invitacion.formulario.error-nombre")).toHaveTextContent(
      "Debe tener al menos 2 caracteres.",
    );
    expect(id("aceptar-invitacion.formulario.nombre")).toHaveFocus();
  });

  it("una invitación vencida o usada: el error traducido", async () => {
    const usuario = abrir("/invitacion?token=kcin_usada");
    await screen.findByTestId("aceptar-invitacion.formulario.codigo");
    await completar(usuario);
    await usuario.click(id("aceptar-invitacion.formulario.enviar"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "La invitación expiró o ya se usó. Pide al administrador que te la reenvíe.",
    );
    expect(screen.queryByTestId("tablero.vista.raiz")).toBeNull();
  });

  it.each([
    ["USUARIO_YA_EXISTE", 409, "Ya existe una cuenta con este correo. Inicia sesión.", true],
    ["LIMITE_USUARIOS_PLAN", 402, "alcanzó el límite de usuarios del plan", false],
  ])("%s: el error traducido", async (codigo, status, texto, conEnlace) => {
    server.use(
      http.post("*/api/v1/auth/invitations/accept", () => sobreError(status, codigo, "x")),
    );
    const usuario = abrir("/invitacion?token=kcin_valido");
    await screen.findByTestId("aceptar-invitacion.formulario.codigo");
    await completar(usuario);
    await usuario.click(id("aceptar-invitacion.formulario.enviar"));

    expect(await screen.findByRole("alert")).toHaveTextContent(texto);
    expect(screen.queryByTestId("aceptar-invitacion.avisos.ir-login") !== null).toBe(conEnlace);
  });

  it("con otra sesión abierta, primero hay que cerrarla", async () => {
    // Con sesión abierta como owner, el enlace del correo lleva a la invitación.
    const usuario = abrir(
      `/login?siguiente=${encodeURIComponent("/invitacion?token=kcin_valido")}`,
    );
    await usuario.type(
      await screen.findByTestId("login.formulario.correo"),
      "owner@demo.kallicode.dev",
    );
    await usuario.type(id("login.formulario.clave"), "Dev12345!demo{Enter}");

    const aviso = await screen.findByTestId("aceptar-invitacion.avisos.sesion");
    expect(aviso).toHaveTextContent("Tienes una sesión abierta como Olga Owner");
    expect(screen.queryByTestId("aceptar-invitacion.formulario.codigo")).toBeNull();

    await usuario.click(id("aceptar-invitacion.avisos.cerrar-sesion"));
    expect(await screen.findByTestId("aceptar-invitacion.formulario.codigo")).toHaveValue(
      "kcin_valido",
    );
  });
});
