import { test as base, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Credenciales canónicas de desarrollo (alineadas con db/seeds/dev.sql).
 */
export const USUARIOS_TEST = {
  owner: {
    email: "owner@demo.kallicode.dev",
    password: "Dev12345!demo",
    nombre: "Olga Owner",
    rol: "owner",
    puede_firmar: [1, 2],
  },
  admin: {
    email: "admin@demo.kallicode.dev",
    password: "Dev12345!demo",
    nombre: "Ana Admin",
    rol: "admin",
    puede_firmar: [1, 2],
  },
  architect: {
    email: "arquitecto@demo.kallicode.dev",
    password: "Dev12345!demo",
    nombre: "Max K.",
    rol: "architect",
    puede_firmar: [1, 2, 3],
  },
  approver: {
    email: "aprobador@demo.kallicode.dev",
    password: "Dev12345!demo",
    nombre: "Alicia Approver",
    rol: "approver",
    puede_firmar: [1, 2],
  },
  viewer: {
    email: "dev@demo.kallicode.dev",
    password: "Dev12345!demo",
    nombre: "María G.",
    rol: "viewer",
    puede_firmar: [],
  },
} as const;

export type RolUsuario = keyof typeof USUARIOS_TEST;

export const AUTH_DIR = path.resolve(__dirname, "../.auth");

/**
 * Intercepta y simula las llamadas a la API REST de KalliCode (/api/v1).
 * Garantiza determinismo 100% Cero-Nube y desacopla la UI de servicios externos.
 */
export async function setupApiMocks(page: Page, rolActivo: RolUsuario = "owner"): Promise<void> {
  const usuarioInfo = USUARIOS_TEST[rolActivo];

  // Capturar logs de consola del navegador para depuración
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      // eslint-disable-next-line no-console
      console.log(`[Browser console.error] ${msg.text()}`);
    }
  });

  const ticketsEnMemoria = [
    {
      id: "KC-1001",
      titulo: "Timeout en búsqueda de clientes",
      tipo: "bug",
      prioridad: "alta",
      etapa: "triage",
      gate_pendiente: null,
      origen: "portal",
      actualizado_en: "2026-09-25T12:00:00Z",
    },
    {
      id: "KC-1002",
      titulo: "Exportar CSV desde listados",
      tipo: "mejora",
      prioridad: "media",
      etapa: "triage",
      gate_pendiente: null,
      origen: "portal",
      actualizado_en: "2026-09-25T12:00:00Z",
    },
    {
      id: "KC-1003",
      titulo: "Facturación por convenio corporativo",
      tipo: "funcionalidad",
      prioridad: "media",
      etapa: "design",
      gate_pendiente: 1,
      origen: "portal",
      actualizado_en: "2026-09-25T12:00:00Z",
    },
  ];

  await page.route("**/api/v1/**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    // 1. Auth: Login
    if (url.includes("/api/v1/auth/login") && method === "POST") {
      const body = route.request().postDataJSON() || {};
      const foundRole = (Object.keys(USUARIOS_TEST) as RolUsuario[]).find(
        (r) => USUARIOS_TEST[r].email === body.email
      );

      if (foundRole && body.password === "Dev12345!demo") {
        const u = USUARIOS_TEST[foundRole];
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            access_token: `mock_jwt_token_${foundRole}`,
            refresh_token: `mock_refresh_token_${foundRole}`,
            usuario: {
              id: `u_${foundRole}`,
              nombre: u.nombre,
              email: u.email,
              rol: u.rol,
              idioma: "es",
              org_id: "org_demo",
            },
            acceso_restringido: false,
          }),
        });
      }

      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            codigo: "AUTH_CREDENCIALES_INVALIDAS",
            mensaje: "Correo o contraseña incorrectos.",
            trace_id: "tr_mock_auth_fail",
            timestamp: new Date().toISOString(),
          },
        }),
      });
    }

    // 2. Users: /users/me
    if (url.includes("/api/v1/users/me") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: `u_${rolActivo}`,
          nombre: usuarioInfo.nombre,
          email: usuarioInfo.email,
          rol: usuarioInfo.rol,
          idioma: "es",
          estado: "activo",
          notificaciones: {},
          puede_firmar: usuarioInfo.puede_firmar,
          ultimo_acceso: new Date().toISOString(),
        }),
      });
    }

    // 3. Organization
    if (url.includes("/api/v1/organization") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "org_demo",
          nombre: "Demo Mobility",
          idioma: "es",
          timezone: "Europe/Madrid",
          despliegue: "saas",
          fabrica_activa: true,
          plan: {
            codigo: "growth",
            nombre: "Growth",
            lineas: 3,
            loc_mes: 20000,
            renueva_el: "2026-12-01",
          },
          polling: { notificaciones_s: 30, tablero_s: 60 },
        }),
      });
    }

    // 4. Notifications
    if (url.includes("/api/v1/notifications") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          no_leidas: 0,
          items: [],
          page: 1,
          page_size: 25,
        }),
      });
    }

    // 5. Approvals Pending
    if (url.includes("/api/v1/approvals/pending") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              ticket_id: "KC-1003",
              titulo: "Facturación por convenio corporativo",
              gate: 1,
              que_se_aprueba: "Diseño y alternativa",
              esperando_desde: "2026-09-25T12:00:00Z",
            },
          ],
        }),
      });
    }

    // 6. Dashboard Summary
    if (url.includes("/api/v1/dashboard/summary") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          en_cola: 2,
          en_proceso: 1,
          esperando_aprobacion: 1,
          en_produccion_30d: 0,
        }),
      });
    }

    // 7. Dashboard Lines
    if (url.includes("/api/v1/dashboard/lines") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          lineas: [
            {
              numero: 1,
              estado: "ocupada",
              ticket_id: "KC-1003",
              etapa: "gate1",
            },
            {
              numero: 2,
              estado: "disponible",
              ticket_id: null,
              etapa: null,
            },
            {
              numero: 3,
              estado: "disponible",
              ticket_id: null,
              etapa: null,
            },
          ],
          en_espera: 0,
          contratadas: 3,
        }),
      });
    }

    // 8. Dashboard Kanban
    if (url.includes("/api/v1/dashboard/kanban") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          columnas: {
            triage: [
              {
                id: "KC-1001",
                titulo: "Timeout en búsqueda de clientes",
                tipo: "bug",
                prioridad: "alta",
                gate_pendiente: null,
              },
              {
                id: "KC-1002",
                titulo: "Exportar CSV desde listados",
                tipo: "mejora",
                prioridad: "media",
                gate_pendiente: null,
              },
            ],
            design: [
              {
                id: "KC-1003",
                titulo: "Facturación por convenio corporativo",
                tipo: "funcionalidad",
                prioridad: "media",
                gate_pendiente: 1,
              },
            ],
            build: [],
            qa: [],
            security: [],
            deploy: [],
          },
        }),
      });
    }

    // 9. Tickets List: GET /api/v1/tickets
    if (url.endsWith("/api/v1/tickets") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: ticketsEnMemoria,
          total: ticketsEnMemoria.length,
          page: 1,
          page_size: 25,
        }),
      });
    }

    // 10. Tickets: Create (POST /api/v1/tickets)
    if (url.endsWith("/api/v1/tickets") && method === "POST") {
      const data = route.request().postDataJSON() || {};
      const newId = `KC-${1000 + ticketsEnMemoria.length + 1}`;
      const newTicket = {
        id: newId,
        titulo: data.titulo || "Nuevo Ticket Sintético",
        tipo: data.tipo || "bug",
        prioridad: data.prioridad || "alta",
        etapa: "triage",
        gate_pendiente: null,
        origen: "portal",
        actualizado_en: new Date().toISOString(),
      };
      ticketsEnMemoria.push(newTicket);

      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: newId,
          etapa: "triage",
        }),
      });
    }

    // 11. Similar tickets check / preview
    if (url.includes("/similar") || url.includes("/similares") || url.includes("/dedup-preview")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          similares: [],
        }),
      });
    }

    // 12. Gate Approve: POST /api/v1/tickets/:id/gates/:gate/approve
    if (url.includes("/gates/") && url.includes("/approve") && method === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          gate: 1,
          sello: "sig_mock_ed25519_gate1_audit_seal_ok",
        }),
      });
    }

    // 13. Ticket Detail Tabs: spec, evidence, security, usage, activity
    if (url.includes("/spec")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          version: 1,
          estado: "pendiente_gate1",
          alternativas: [
            {
              id: "A",
              titulo: "Tabla de convenios",
              descripcion: "Nueva tabla y regla de precio",
              riesgo: "bajo",
              recomendada: true,
              elegida: false,
            },
            {
              id: "B",
              titulo: "Descuento por cliente",
              descripcion: "Reutilizar descuentos existentes",
              riesgo: "medio",
              recomendada: false,
              elegida: false,
            },
          ],
          criterios: [
            {
              id: "CRIT-01",
              given: "un cliente con convenio",
              when: "se factura",
              then: "aplica la tarifa del convenio",
            },
          ],
          scope_paths: ["billing/"],
          estimacion_loc: 320,
        }),
      });
    }

    if (url.includes("/evidence")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          corrida: 1,
          ejecutada_en: "2026-09-25T12:00:00Z",
          matriz: [
            {
              criterio_id: "CRIT-01",
              resultado: "pasa",
              extracto_fallo: null,
              evidencias: [],
            },
          ],
          regresion: {
            dirigidos: 12,
            dirigidos_ok: 12,
            suite_cliente: 40,
            cliente_ok: 40,
          },
          flaky: [],
        }),
      });
    }

    if (url.includes("/security")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          hallazgos: [
            {
              id: "SEC-001",
              severidad: "baja",
              titulo: "Verificación de secretos en código fuente",
              origen: "secrets",
              estado: "abierto",
              justificacion: null,
            },
          ],
          bloquea_deploy: false,
        }),
      });
    }

    if (url.includes("/usage")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          totales: {
            tokens_in: 12000,
            tokens_out: 4000,
            invocaciones: 8,
            loc_netas: 320,
            loc_tests: 150,
          },
          por_funcion: [
            {
              funcion: "triage.classify",
              modelo: "deepseek-v4-flash",
              invocaciones: 1,
              tokens_in: 400,
              tokens_out: 200,
              duracion_media_ms: 900,
            },
          ],
          desglose_loc: {
            anadidas: 350,
            eliminadas: 30,
            netas: 320,
            tests: 150,
          },
        }),
      });
    }

    if (url.includes("/activity")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              fecha: "2026-09-25T12:00:00Z",
              actor: { tipo: "humano", nombre: "María G." },
              evento: "ticket.creado",
              etapa: "triage",
            },
          ],
          page: 1,
          page_size: 25,
        }),
      });
    }

    // 14. Ticket Detail: /api/v1/tickets/:id
    const ticketDetailMatch = url.match(/\/api\/v1\/tickets\/([A-Za-z0-9-]+)$/);
    if (ticketDetailMatch && method === "GET") {
      const tid = ticketDetailMatch[1];
      const isKC1003 = tid === "KC-1003";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: tid,
          titulo: isKC1003
            ? "Facturación por convenio corporativo"
            : "Timeout en búsqueda de clientes",
          tipo: isKC1003 ? "funcionalidad" : "bug",
          prioridad: "alta",
          origen: "portal",
          reportado_por: { id: "u_dev", nombre: "María G." },
          etapa: isKC1003 ? "design" : "triage",
          gate_pendiente: isKC1003 ? 1 : null,
          pipeline: [
            { etapa: "triage", estado: "done", gate: null },
            { etapa: "design", estado: isKC1003 ? "actual" : "pendiente", gate: 1 },
            { etapa: "build", estado: "pendiente", gate: 2 },
            { etapa: "qa", estado: "pendiente", gate: null },
            { etapa: "security", estado: "pendiente", gate: null },
            { etapa: "deploy", estado: "pendiente", gate: 3 },
          ],
          detalle: {
            linea: 1,
            radio_impacto: null,
            estimacion: null,
            pr_url: null,
          },
          visualizadores: [{ id: "u_owner", nombre: "Olga Owner" }],
        }),
      });
    }

    // 15. Connections: /api/v1/connections
    if (url.includes("/api/v1/connections") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conexiones: [
            {
              id: "conn-github",
              categoria: "repositorio",
              proveedor: "github",
              nombre: "GitHub Org",
              estado: "conectado",
              ultimo_test: "2026-09-25T12:00:00Z",
            },
            {
              id: "conn-jira",
              categoria: "tickets",
              proveedor: "jira",
              nombre: "Jira Workspace",
              estado: "conectado",
              ultimo_test: "2026-09-25T12:00:00Z",
            },
            {
              id: "conn-actions",
              categoria: "cicd",
              proveedor: "github_actions",
              nombre: "CI/CD Pipeline",
              estado: "conectado",
              ultimo_test: "2026-09-25T12:00:00Z",
            },
            {
              id: "conn-postgres",
              categoria: "database",
              proveedor: "postgresql",
              nombre: "Dev Postgres",
              estado: "conectado",
              ultimo_test: "2026-09-25T12:00:00Z",
            },
          ],
          mapa_ramas: {
            feature: "feature/*",
            staging: "staging",
            produccion: "main",
          },
        }),
      });
    }

    // Fallback: continue request
    return route.continue();
  });
}

/**
 * Inicia sesión en el portal cliente mediante UI, utilizando los selectores del contrato oficial.
 */
export async function loginAs(page: Page, rol: RolUsuario): Promise<void> {
  const usuario = USUARIOS_TEST[rol];
  await setupApiMocks(page, rol);

  await page.goto("/login");
  await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

  await page.getByTestId("login.formulario.correo").fill(usuario.email);
  await page.getByTestId("login.formulario.clave").fill(usuario.password);
  await page.getByTestId("login.formulario.entrar").click();

  // Espera web-first al aterrizaje en el Tablero principal
  await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
}

/**
 * Navega dentro de la SPA sin recargar el navegador para preservar la sesión en memoria.
 */
export async function navigateSpa(page: Page, targetUrl: string): Promise<void> {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, targetUrl);
}

/**
 * Guarda el storageState en disco para reutilización de sesión.
 */
export async function persistStorageState(page: Page, rol: RolUsuario): Promise<string> {
  if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
  }
  const filePath = path.join(AUTH_DIR, `user_${rol}.json`);
  await page.context().storageState({ path: filePath });
  return filePath;
}

/**
 * Fixture extendido de Playwright con usuarios y páginas preconfiguradas.
 */
export const test = base.extend<{
  ownerPage: Page;
  approverPage: Page;
}>({
  ownerPage: async ({ page }, use) => {
    await loginAs(page, "owner");
    await use(page);
  },
  approverPage: async ({ page }, use) => {
    await loginAs(page, "approver");
    await use(page);
  },
});

export { expect };
