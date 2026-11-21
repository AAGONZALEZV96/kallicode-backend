/**
 * Onboarding completo de un tenant nuevo (KC-PT-07-7), en el portal completo: de 0 de 5 a
 * la fábrica encendida, con un doble del backend que guarda lo que se le manda y responde
 * como connections.py (mismas formas y precondiciones).
 *
 * Lo que el portal manda se compara con apps/api/tests/integracion/onboarding_desde_portal.json,
 * el mismo archivo que test_onboarding_tenant_nuevo.py (al lado) manda al backend real. Si el portal
 * cambia un cuerpo y el backend no lo acepta, falla una de las dos pruebas (así se habría
 * detectado el desfase de claves con los verificadores de KC-BE-07-1).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { organizacion } from "../../mocks/datos";
import { sobreError } from "../../mocks/handlers";
import { server } from "../../mocks/server";
import { entrarDesde, rutaActual } from "../../test/portal";
import { proveedor } from "./proveedores";

interface Solicitud {
  paso: string;
  metodo: string;
  ruta: string;
  cuerpo: Record<string, unknown> | null;
}
interface CuerpoConexion {
  proveedor: string;
  nombre?: string;
  config: Record<string, string>;
  credenciales: Record<string, string>;
}

const ESPERADO: { solicitudes: Solicitud[] } = JSON.parse(
  readFileSync(resolve("../apps/api/tests/integracion/onboarding_desde_portal.json"), "utf-8"),
);
const cuerpoDe = (paso: string) =>
  ESPERADO.solicitudes.find((s) => s.paso === paso)!.cuerpo as unknown as CuerpoConexion;

const id = (e: string) => screen.getByTestId(`onboarding.${e}`);
const progreso = () => id("progreso.barra").getAttribute("aria-valuenow");
const actual = () =>
  screen
    .getAllByTestId("onboarding.pasos.paso")
    .findIndex((p) => p.getAttribute("aria-current") === "step");

/**
 * Doble del backend de un tenant recién creado: sin conexiones, sin mapa de ramas, sin
 * documentos y con la fábrica apagada. Guarda lo que recibe y aplica las precondiciones de
 * connections.completar. Registra las solicitudes que cambian datos del asistente.
 */
function backendDeTenantNuevo() {
  const estado = {
    conexiones: [] as Array<Record<string, unknown>>,
    mapa: null as Record<string, string> | null,
    fabricaActiva: false,
  };
  const recibidas: Omit<Solicitud, "paso">[] = [];
  const registrar = async (request: Request) => {
    const cuerpo = request.method === "GET" ? null : await request.clone().text();
    recibidas.push({
      metodo: request.method,
      ruta: new URL(request.url).pathname,
      cuerpo: cuerpo ? JSON.parse(cuerpo) : null,
    });
  };

  server.use(
    http.get("*/api/v1/connections", () =>
      HttpResponse.json({ conexiones: estado.conexiones, mapa_ramas: estado.mapa }),
    ),
    http.post("*/api/v1/connections", async ({ request }) => {
      await registrar(request);
      const cuerpo = (await request.json()) as { categoria: string } & CuerpoConexion;
      const cid = `cn_${estado.conexiones.length + 1}`;
      estado.conexiones.push({
        id: cid,
        categoria: cuerpo.categoria,
        proveedor: cuerpo.proveedor,
        nombre: cuerpo.nombre ?? null,
        estado: "conectada",
        ultimo_test: "2026-11-20T12:00:00Z",
      });
      const conWebhook = ["tickets", "cicd"].includes(cuerpo.categoria);
      return HttpResponse.json(
        {
          id: cid,
          estado: "conectada",
          permisos_verificados: ["repo:read"],
          ...(conWebhook ? { webhook_token: `wh_${cid}` } : {}),
        },
        { status: 201 },
      );
    }),
    http.put("*/api/v1/connections/branch-map", async ({ request }) => {
      await registrar(request);
      estado.mapa = (await request.json()) as Record<string, string>;
      return HttpResponse.json({ mapa_ramas: estado.mapa });
    }),
    http.get("*/api/v1/organization", () =>
      HttpResponse.json({ ...organizacion, fabrica_activa: estado.fabricaActiva }),
    ),
    http.get("*/api/v1/onboarding/codemapping/status", () =>
      HttpResponse.json({
        fase: 7,
        progreso: 1,
        metricas: { definiciones: 0, documentos: 0 },
        curacion_pendiente: 0,
        ultimo_refresh: null,
      }),
    ),
    http.post("*/api/v1/onboarding/complete", async ({ request }) => {
      await registrar(request);
      const pendientes = [
        ...(estado.conexiones.some((c) => c.categoria === "repositorio") ? [] : ["repositorio"]),
        ...(estado.mapa ? [] : ["mapa_ramas"]),
      ];
      if (pendientes.length) {
        return sobreError(409, "ONBOARDING_INCOMPLETO", "Faltan pasos para encender la fábrica.");
      }
      estado.fabricaActiva = true;
      return HttpResponse.json({ activado: true, lineas_activadas: 3, pendientes: [] });
    }),
  );
  return { estado, recibidas };
}

/** Llena el formulario de bóveda con los valores del cuerpo esperado, campo por campo. */
async function llenarConexion(usuario: UserEvent, cuerpo: CuerpoConexion) {
  expect(id("formulario.proveedor")).toHaveValue(cuerpo.proveedor);
  if (cuerpo.nombre) await usuario.type(id("formulario.nombre"), cuerpo.nombre);
  for (const campo of proveedor(cuerpo.proveedor)!.campos) {
    const valor = cuerpo[campo.destino][campo.clave];
    if (valor) await usuario.type(id(`formulario.${campo.clave.replace(/_/g, "-")}`), valor);
  }
  await usuario.click(id("formulario.enviar"));
  await screen.findByTestId("onboarding.formulario.exito");
}

async function entrar() {
  const usuario = await entrarDesde("/onboarding");
  await screen.findByTestId("onboarding.panel.contenido");
  return usuario;
}

describe("onboarding de un tenant nuevo", () => {
  it("va de 0 de 5 a la fábrica encendida y manda exactamente lo acordado con el backend", async () => {
    const backend = backendDeTenantNuevo();
    let usuario = await entrar();

    // Tenant nuevo: paso 1, nada hecho, encender bloqueado.
    expect(actual()).toBe(0);
    expect(progreso()).toBe("0");

    // 1. Repositorio.
    await usuario.click(id("formulario.abrir"));
    await llenarConexion(usuario, cuerpoDe("repositorio"));
    await usuario.click(within(id("formulario.exito")).getByRole("button"));
    await waitFor(() => expect(progreso()).toBe("1"));
    expect(id("resumen.conexion")).toHaveTextContent("GitHub · app principal Conectada");

    // 2. Herramientas (opcional): Jira, con la URL de su webhook.
    await usuario.click(id("navegacion.siguiente"));
    await usuario.click(
      within(id("categorias.tickets")).getByTestId("onboarding.formulario.abrir"),
    );
    await llenarConexion(usuario, cuerpoDe("herramientas"));
    expect(id("formulario.webhook")).toHaveValue(
      `${import.meta.env.VITE_API_URL}/api/v1/webhooks/jira/wh_cn_2`,
    );
    await usuario.click(within(id("formulario.exito")).getByRole("button"));
    await waitFor(() => expect(progreso()).toBe("2"));

    // Se corta la sesión a mitad: al volver, retoma en el primer obligatorio pendiente.
    cleanup();
    usuario = await entrar();
    expect(actual()).toBe(2);
    expect(progreso()).toBe("2");

    // 3. Mapa de ramas: se guardan los valores sugeridos.
    expect(id("resumen.vacio")).toBeInTheDocument();
    await usuario.click(id("ramas.guardar"));
    await waitFor(() => expect(id("ramas.exito")).toHaveTextContent("Mapa de ramas guardado."));
    await waitFor(() => expect(progreso()).toBe("3"));

    // 4. Documentación (opcional): sin documentos, se puede seguir.
    await usuario.click(id("navegacion.siguiente"));
    expect(id("resumen.documentos")).toHaveTextContent("Documentos subidos: 0");

    // 5. Activación: sin pasos pendientes, se enciende con confirmación.
    await usuario.click(id("navegacion.siguiente"));
    expect(screen.queryByTestId("onboarding.activacion.pendientes")).not.toBeInTheDocument();
    await usuario.click(id("activacion.encender"));
    await usuario.click(id("encendido.confirmar"));
    await waitFor(() =>
      expect(id("activacion.resultado")).toHaveTextContent(
        "Fábrica encendida con 3 líneas de producción.",
      ),
    );
    await waitFor(() => expect(id("activacion.tablero")).toHaveFocus());
    expect(progreso()).toBe("4");
    expect(backend.estado.fabricaActiva).toBe(true);

    await usuario.click(id("activacion.tablero"));
    await screen.findByTestId("tablero.vista.raiz");
    expect(rutaActual()).toBe("/");

    // Lo que recibió el backend es, en orden, lo acordado en el archivo compartido.
    expect(backend.recibidas).toEqual(
      ESPERADO.solicitudes.map(({ metodo, ruta, cuerpo }) => ({ metodo, ruta, cuerpo })),
    );
    // El recorrido completo (dos inicios de sesión y cinco pasos) tarda más que el límite por
    // defecto de 5 s.
  }, 30_000);

  it("sin repositorio no se puede encender, y el asistente lleva a lo que falta", async () => {
    const backend = backendDeTenantNuevo();
    const usuario = await entrarDesde("/onboarding?paso=5");
    await screen.findByTestId("onboarding.panel.contenido");

    expect(id("activacion.pendientes")).toHaveTextContent("Repositorio");
    expect(id("activacion.pendientes")).toHaveTextContent("Mapa de ramas");
    await usuario.click(id("activacion.encender"));
    expect(screen.queryByTestId("onboarding.encendido.dialogo")).not.toBeInTheDocument();

    await usuario.click(screen.getAllByTestId("onboarding.activacion.ir")[0]!);
    expect(actual()).toBe(0);
    expect(backend.recibidas).toEqual([]);
  });
});
