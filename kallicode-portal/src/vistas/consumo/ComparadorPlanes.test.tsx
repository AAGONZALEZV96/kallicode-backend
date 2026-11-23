/**
 * Comparador de planes y solicitud de mejora (KC-PT-09-1), en el portal completo: GET /plans
 * se ordena de menor a mayor, solo se ofrecen planes superiores al vigente, la solicitud lleva
 * el plan elegido y el comentario, y una solicitud abierta se muestra en Consumo.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Rol } from "../../api/tipos";
import { perfilOwner, planes, solicitudMejora } from "../../mocks/datos";
import { server } from "../../mocks/server";
import { entrarDesde } from "../../test/portal";

const dialogo = () => screen.getByTestId("consumo.planes.dialogo");
const tarjetas = () => within(dialogo()).getAllByTestId("consumo.planes.plan");
const opciones = () => within(dialogo()).queryAllByTestId("consumo.planes.opcion");
/** Intl separa con espacios no separables: se comparan como espacios. */
const texto = (el: Element) => (el.textContent ?? "").replace(/\s/g, " ");

function conRol(rol: Rol) {
  server.use(http.get("*/api/v1/users/me", () => HttpResponse.json({ ...perfilOwner, rol })));
}

/** GET /plans con otro plan vigente (el delta no importa en estas pruebas). */
function vigente(codigo: string | null) {
  server.use(
    http.get("*/api/v1/plans", () =>
      HttpResponse.json({
        planes: planes.planes.map((p) => ({ ...p, actual: p.codigo === codigo })),
      }),
    ),
  );
}

/** Rutas de /plans pedidas y cuerpos enviados a POST /plans/upgrade-request. */
function registrar() {
  const rutas: string[] = [];
  const cuerpos: unknown[] = [];
  server.events.on("request:start", async ({ request }) => {
    const u = new URL(request.url);
    if (!u.pathname.startsWith("/api/v1/plans")) return;
    rutas.push(`${request.method} ${u.pathname}`);
    if (request.method === "POST") cuerpos.push(await request.clone().json());
  });
  return { rutas, cuerpos };
}

async function abrir(rol: Rol = "owner") {
  conRol(rol);
  const usuario = await entrarDesde("/consumo");
  const boton = await screen.findByTestId(
    rol === "owner" || rol === "admin" ? "consumo.cabecera.upgrade" : "consumo.cabecera.planes",
  );
  await usuario.click(boton);
  await within(dialogo()).findAllByTestId("consumo.planes.plan");
  return { usuario, boton };
}

describe("comparador: los planes y sus diferencias", () => {
  it("de menor a mayor, con el vigente marcado y la diferencia con él", async () => {
    await abrir();

    expect(dialogo()).toHaveAttribute("role", "dialog");
    expect(within(dialogo()).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Comparar planes",
    );
    // El backend los manda alfabéticos (enterprise, growth, starter).
    const [starter, growth, enterprise] = tarjetas();
    expect(within(starter!).getByRole("heading")).toHaveTextContent("Starter");
    expect(within(enterprise!).getByRole("heading")).toHaveTextContent("Enterprise");

    expect(growth).toHaveAttribute("aria-current", "true");
    expect(growth).toHaveTextContent("Tu plan");
    expect(texto(growth!)).toContain("LOC al mes20.000");
    expect(texto(growth!)).toContain("Almacenamiento de evidencia25 GB");
    // El vigente no se compara consigo mismo.
    expect(growth!.querySelector(".plan-delta")).toBeNull();

    // Starter trae menos: «−17.000» LOC, «−2» líneas, «−20 GB», dicho también en palabras.
    expect(starter).not.toHaveAttribute("aria-current");
    const menos = [...starter!.querySelectorAll(".plan-delta")].map(texto);
    expect(menos).toEqual([
      "−17.000 menos que tu plan",
      "−2 menos que tu plan",
      "−20 menos que tu plan",
      "−20 GB menos que tu plan",
    ]);
    expect(texto(starter!)).toContain("LOC al mes3000");

    // Enterprise no fija límites: «A medida» o «Ilimitados», sin diferencia.
    expect(texto(enterprise!)).toContain("LOC al mesA medida");
    expect(texto(enterprise!)).toContain("Líneas de fábricaIlimitadas");
    expect(texto(enterprise!)).toContain("UsuariosIlimitados");
    expect(enterprise!.querySelector(".plan-delta")).toBeNull();
    expect(enterprise).toHaveTextContent("DespliegueNube, Híbrido, En tus servidores");
  });

  it("un plan con más capacidad lo muestra con «+»", async () => {
    server.use(
      http.get("*/api/v1/plans", () =>
        HttpResponse.json({
          planes: planes.planes.map((p) =>
            p.codigo === "growth"
              ? {
                  ...p,
                  actual: false,
                  delta: { loc_mes: 17000, lineas: 2, max_usuarios: 20, storage_mb: 20480 },
                }
              : { ...p, actual: p.codigo === "starter" },
          ),
        }),
      ),
    );
    await abrir();

    const growth = tarjetas()[1]!;
    expect([...growth.querySelectorAll(".plan-delta.mas")].map(texto)).toEqual([
      "+17.000 más que tu plan",
      "+2 más que tu plan",
      "+20 más que tu plan",
      "+20 GB más que tu plan",
    ]);
  });

  it("un plan que el portal no conoce va al final y no se ofrece", async () => {
    server.use(
      http.get("*/api/v1/plans", () =>
        HttpResponse.json({
          planes: [
            { ...planes.planes[2]!, codigo: "a_medida", nombre: "A medida", actual: false },
            ...planes.planes.map((p) => ({ ...p, actual: p.codigo === "starter" })),
          ],
        }),
      ),
    );
    await abrir();

    expect(tarjetas().map((t) => within(t).getByRole("heading").textContent)).toEqual([
      "Starter",
      "Growth",
      "Enterprise",
      "A medida",
    ]);
    // El backend solo acepta starter, growth o enterprise (UpgradeIn).
    expect(opciones().map((o) => (o as HTMLInputElement).value)).toEqual(["growth", "enterprise"]);
  });

  it("una modalidad de despliegue desconocida se muestra tal cual", async () => {
    server.use(
      http.get("*/api/v1/plans", () =>
        HttpResponse.json({
          planes: planes.planes.map((p) => ({ ...p, despliegues: ["cloud", "soberano"] })),
        }),
      ),
    );
    await abrir();

    expect(tarjetas()[0]).toHaveTextContent("DespliegueNube, soberano");
  });

  it("GET /plans se pide al abrir el comparador, no al entrar a Consumo", async () => {
    const { rutas } = registrar();
    conRol("owner");
    const usuario = await entrarDesde("/consumo");
    await screen.findByTestId("consumo.vista.raiz");
    expect(rutas).not.toContain("GET /api/v1/plans");

    await usuario.click(screen.getByTestId("consumo.cabecera.upgrade"));
    await within(dialogo()).findAllByTestId("consumo.planes.plan");
    expect(rutas).toContain("GET /api/v1/plans");
  });

  it("mientras carga, la silueta en el diálogo; se puede cerrar igual", async () => {
    server.use(http.get("*/api/v1/plans", () => delay("infinite")));
    conRol("owner");
    const usuario = await entrarDesde("/consumo");
    await usuario.click(await screen.findByTestId("consumo.cabecera.upgrade"));

    expect(within(dialogo()).getByTestId("comun.carga.indicador")).toHaveTextContent("Cargando…");
    await usuario.click(within(dialogo()).getByTestId("consumo.planes.cancelar"));
    expect(screen.queryByTestId("consumo.planes.dialogo")).not.toBeInTheDocument();
  });

  it("si GET /plans falla, el error queda en el diálogo y se puede cerrar", async () => {
    server.use(
      http.get("*/api/v1/plans", () =>
        HttpResponse.json(
          { error: { codigo: "ERROR_INTERNO", mensaje: "Falló", trace_id: "t" } },
          { status: 500 },
        ),
      ),
    );
    conRol("owner");
    const usuario = await entrarDesde("/consumo");
    await usuario.click(await screen.findByTestId("consumo.cabecera.upgrade"));

    expect(await within(dialogo()).findByRole("alert")).toBeInTheDocument();
    // La vista sigue mostrando el consumo detrás.
    expect(screen.getByTestId("consumo.kpis.consumidas")).toBeInTheDocument();
    await usuario.click(within(dialogo()).getByTestId("consumo.planes.cancelar"));
    expect(screen.queryByTestId("consumo.planes.dialogo")).not.toBeInTheDocument();
  });
});

describe("solicitar la mejora", () => {
  it("cada control del formulario tiene nombre accesible y las opciones forman un grupo", async () => {
    vigente("starter");
    await abrir();

    const controles = ["radio", "textbox", "button"].flatMap((rol) =>
      within(dialogo()).getAllByRole(rol),
    );
    expect(controles).toHaveLength(2 + 1 + 2);
    for (const control of controles) expect(control).toHaveAccessibleName();
    expect(
      within(dialogo()).getByRole("group", { name: "Elige el plan al que quieres mejorar" }),
    ).toContainElement(opciones()[0]!);
    expect(within(dialogo()).getByRole("textbox")).toHaveAccessibleName(
      "Comentario para el equipo comercial (opcional)",
    );
  });

  it("solo se ofrecen los planes superiores; por defecto, el siguiente", async () => {
    vigente("starter");
    await abrir();

    // Starter (vigente) no tiene opción; growth y enterprise sí, con growth elegido.
    expect(within(tarjetas()[0]!).queryByTestId("consumo.planes.opcion")).toBeNull();
    expect(opciones().map((o) => (o as HTMLInputElement).value)).toEqual(["growth", "enterprise"]);
    expect(opciones()[0]).toBeChecked();
    expect(within(dialogo()).getByRole("radio", { name: "Solicitar Growth" })).toBeChecked();
    // La opción se describe con los límites de su plan.
    const descrito = document.getElementById(opciones()[0]!.getAttribute("aria-describedby")!);
    expect(descrito).toHaveTextContent("LOC al mes");
  });

  it("envía el plan elegido con el comentario, cierra y muestra la respuesta y la solicitud", async () => {
    vigente("starter");
    let abierta: typeof solicitudMejora | null = null;
    server.use(
      http.get("*/api/v1/plans/upgrade-request", () => HttpResponse.json(abierta)),
      http.post("*/api/v1/plans/upgrade-request", async ({ request }) => {
        const cuerpo = (await request.clone().json()) as { plan_deseado: string };
        abierta = { ...solicitudMejora, plan_deseado: cuerpo.plan_deseado };
        return HttpResponse.json(
          {
            solicitud_id: "upg_1",
            estado: "registrada",
            mensaje: "El equipo comercial revisará tu solicitud.",
          },
          { status: 202 },
        );
      }),
    );
    const { cuerpos } = registrar();
    const { usuario, boton } = await abrir();

    await usuario.click(within(dialogo()).getByRole("radio", { name: "Solicitar Enterprise" }));
    await usuario.type(
      within(dialogo()).getByTestId("consumo.planes.comentario"),
      "  Necesitamos on-premise en enero  ",
    );
    await usuario.click(within(dialogo()).getByTestId("consumo.planes.enviar"));

    await waitFor(() =>
      expect(screen.queryByTestId("consumo.planes.dialogo")).not.toBeInTheDocument(),
    );
    expect(cuerpos).toEqual([
      { plan_deseado: "enterprise", comentario: "Necesitamos on-premise en enero" },
    ]);
    expect(screen.getByTestId("consumo.avisos.mensaje")).toHaveTextContent(
      "El equipo comercial revisará tu solicitud.",
    );
    // La solicitud nueva se vuelve a leer y queda a la vista.
    const aviso = await screen.findByTestId("consumo.avisos.solicitud");
    expect(aviso).toHaveTextContent("Solicitud de mejora a Enterprise en curso");
    // No se pidió con la cuota agotada: no es prioritaria.
    expect(aviso).not.toHaveTextContent("Prioritaria");
    expect(boton).toHaveFocus();
  });

  it("sin comentario no lo envía", async () => {
    const { cuerpos } = registrar();
    const { usuario } = await abrir();

    await usuario.type(within(dialogo()).getByTestId("consumo.planes.comentario"), "   ");
    await usuario.click(within(dialogo()).getByTestId("consumo.planes.enviar"));

    await waitFor(() => expect(cuerpos).toEqual([{ plan_deseado: "enterprise" }]));
  });

  it("mientras se envía no se puede cerrar ni cambiar el plan", async () => {
    vigente("starter");
    server.use(http.post("*/api/v1/plans/upgrade-request", () => delay("infinite")));
    const { usuario } = await abrir();

    await usuario.click(within(dialogo()).getByTestId("consumo.planes.enviar"));

    const enviar = within(dialogo()).getByTestId("consumo.planes.enviar");
    expect(enviar).toBeDisabled();
    expect(enviar).toHaveTextContent("Cargando…");
    expect(within(dialogo()).getByTestId("consumo.planes.cancelar")).toBeDisabled();
    expect(opciones().every((o) => (o as HTMLInputElement).disabled)).toBe(true);
    // Ni Escape (en el diálogo) ni un clic en el fondo lo cierran.
    fireEvent.keyDown(dialogo(), { key: "Escape" });
    fireEvent.mouseDown(dialogo().parentElement!);
    expect(dialogo()).toBeInTheDocument();
  });

  it("un error (PLAN_NO_SUPERIOR) queda en el diálogo, traducido", async () => {
    server.use(
      http.post("*/api/v1/plans/upgrade-request", () =>
        HttpResponse.json(
          {
            error: {
              codigo: "PLAN_NO_SUPERIOR",
              mensaje: "El plan solicitado requiere una suscripción inferior.",
              trace_id: "t",
            },
          },
          { status: 409 },
        ),
      ),
    );
    const { usuario } = await abrir();

    await usuario.click(within(dialogo()).getByTestId("consumo.planes.enviar"));

    expect(await within(dialogo()).findByRole("alert")).toHaveTextContent(
      "Solo puedes solicitar un plan superior al que tienes.",
    );
    expect(screen.queryByTestId("consumo.avisos.mensaje")).not.toBeInTheDocument();
    // La vista no se reemplaza por el error (antes el error del upgrade tapaba todo Consumo).
    expect(screen.getByTestId("consumo.kpis.consumidas")).toBeInTheDocument();
  });

  it("Cancelar y Escape cierran sin solicitar y devuelven el foco al botón", async () => {
    const { cuerpos } = registrar();
    const { usuario, boton } = await abrir();

    await usuario.click(within(dialogo()).getByTestId("consumo.planes.cancelar"));
    expect(screen.queryByTestId("consumo.planes.dialogo")).not.toBeInTheDocument();
    expect(boton).toHaveFocus();

    await usuario.click(boton);
    await within(dialogo()).findAllByTestId("consumo.planes.plan");
    await usuario.keyboard("{Escape}");
    expect(screen.queryByTestId("consumo.planes.dialogo")).not.toBeInTheDocument();
    expect(cuerpos).toEqual([]);
  });
});

describe("cuando no se puede solicitar", () => {
  it("con una solicitud abierta, Consumo la muestra y el comparador no ofrece otra", async () => {
    server.use(
      http.get("*/api/v1/plans/upgrade-request", () =>
        HttpResponse.json({ ...solicitudMejora, estado: "en_gestion", urgente: true }),
      ),
    );
    conRol("admin");
    const usuario = await entrarDesde("/consumo");

    const aviso = await screen.findByTestId("consumo.avisos.solicitud");
    expect(aviso).toHaveTextContent(
      /Solicitud de mejora a Enterprise en curso desde el 20 .+ 2026/,
    );
    expect(aviso).toHaveTextContent("Prioritaria: cuota agotada");

    await usuario.click(screen.getByTestId("consumo.cabecera.upgrade"));
    await within(dialogo()).findAllByTestId("consumo.planes.plan");
    expect(opciones()).toHaveLength(0);
    expect(within(dialogo()).queryByTestId("consumo.planes.enviar")).toBeNull();
    expect(within(dialogo()).getByTestId("consumo.planes.nota")).toHaveTextContent(
      "Ya hay una solicitud de mejora a Enterprise en curso",
    );
    expect(within(dialogo()).getByTestId("consumo.planes.cancelar")).toHaveTextContent("Cerrar");
  });

  it.each(["aplicada", "descartada"])(
    "una solicitud %s no se muestra como abierta",
    async (estado) => {
      server.use(
        http.get("*/api/v1/plans/upgrade-request", () =>
          HttpResponse.json({ ...solicitudMejora, estado }),
        ),
      );
      await abrir();

      expect(screen.queryByTestId("consumo.avisos.solicitud")).not.toBeInTheDocument();
      expect(opciones()).toHaveLength(1);
    },
  );

  it.each<[Rol]>([["architect"], ["member"]])(
    "un %s compara sin solicitar y no pide la solicitud de mejora",
    async (rol) => {
      const { rutas } = registrar();
      await abrir(rol);

      expect(screen.queryByTestId("consumo.cabecera.upgrade")).not.toBeInTheDocument();
      expect(screen.getByTestId("consumo.cabecera.planes")).toHaveTextContent("Ver planes");
      expect(tarjetas()).toHaveLength(3);
      expect(opciones()).toHaveLength(0);
      expect(within(dialogo()).getByTestId("consumo.planes.nota")).toHaveTextContent(
        "Solo un owner o un admin puede solicitar un cambio de plan.",
      );
      // GET /plans/upgrade-request es solo para owner y admin: no se pide para recibir un 403.
      expect(rutas).not.toContain("GET /api/v1/plans/upgrade-request");
    },
  );

  it("con el plan más alto lo dice", async () => {
    vigente("enterprise");
    await abrir();

    expect(opciones()).toHaveLength(0);
    expect(within(dialogo()).getByTestId("consumo.planes.nota")).toHaveTextContent(
      "Ya tienes el plan más alto.",
    );
  });

  it("sin plan vigente no ofrece mejoras (el backend las rechazaría)", async () => {
    vigente(null);
    await abrir();

    expect(opciones()).toHaveLength(0);
    expect(within(dialogo()).getByTestId("consumo.planes.nota")).toHaveTextContent(
      "Tu organización no tiene un plan vigente",
    );
  });
});
