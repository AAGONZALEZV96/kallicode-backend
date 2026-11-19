import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import type { Page } from "@playwright/test";

export interface OnboardingConnectionItem {
  id: string;
  categoria: "repositorio" | "tickets" | "cicd" | "database";
  proveedor: string;
  nombre: string | null;
  estado: string;
  ultimo_test: string | null;
}

export interface OnboardingAuditEvent {
  evento: string;
  resumen: string;
  fecha: string;
}

export interface OnboardingMockState {
  repoPath: string;
  localBranches: string[];
  conexiones: OnboardingConnectionItem[];
  mapaRamas: { feature: string; staging: string; produccion: string } | null;
  documentos: Array<{ id: string; nombre_archivo: string; tipo_documento: string }>;
  fabricaActiva: boolean;
  lineasActivadas: number;
  auditEvents: OnboardingAuditEvent[];
  mockVaultSecrets: Record<string, Record<string, unknown>>;
}

export const SAMPLE_REPO_PATH = "/tmp/sample-repo";
export const SAMPLE_REPO_HTTPS_URL = "https://git.local.kallicode.test/sample-repo";

/**
 * Inicializa un repositorio Git sintético local (Cero-Nube) con ramas `main`, `develop`
 * y commits reales en `/tmp/sample-repo` (o ruta aislada), retornando las ramas reales.
 */
export function initSyntheticSampleRepo(repoPath: string = SAMPLE_REPO_PATH): string[] {
  fs.rmSync(repoPath, { recursive: true, force: true });
  fs.mkdirSync(path.join(repoPath, "docs"), { recursive: true });
  fs.mkdirSync(path.join(repoPath, "src"), { recursive: true });

  fs.writeFileSync(
    path.join(repoPath, "README.md"),
    "# Sample Synthetic Repo\nRepositorio local Cero-Nube para E2E KC-QA-B5-06.\n",
    "utf-8",
  );
  fs.writeFileSync(
    path.join(repoPath, "docs", "arquitectura.md"),
    "# Arquitectura Base\nBackend FastAPI + PostgreSQL 16 RLS + Redis.\n",
    "utf-8",
  );
  fs.writeFileSync(
    path.join(repoPath, "src", "app.py"),
    "def health() -> dict[str, str]:\n    return {'status': 'ok'}\n",
    "utf-8",
  );

  execFileSync("git", ["init", "-b", "main", repoPath], { stdio: "pipe" });
  const gitEnv = {
    ...process.env,
    GIT_AUTHOR_NAME: "KalliCode QA",
    GIT_AUTHOR_EMAIL: "qa@kallicode.local",
    GIT_COMMITTER_NAME: "KalliCode QA",
    GIT_COMMITTER_EMAIL: "qa@kallicode.local",
  };
  execFileSync("git", ["-C", repoPath, "add", "."], { stdio: "pipe", env: gitEnv });
  execFileSync("git", ["-C", repoPath, "commit", "-m", "feat(synthetic): seed repo"], {
    stdio: "pipe",
    env: gitEnv,
  });
  execFileSync("git", ["-C", repoPath, "branch", "develop"], { stdio: "pipe", env: gitEnv });
  execFileSync("git", ["-C", repoPath, "branch", "feature/demo"], { stdio: "pipe", env: gitEnv });

  const refsRaw = execFileSync(
    "git",
    ["-C", repoPath, "for-each-ref", "--format=%(refname:short)", "refs/heads"],
    { encoding: "utf-8", stdio: "pipe" },
  );
  return refsRaw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/**
 * Elimina el repositorio Git sintético local en teardown.
 */
export function cleanupSyntheticSampleRepo(repoPath: string = SAMPLE_REPO_PATH): void {
  fs.rmSync(repoPath, { recursive: true, force: true });
}

/**
 * Crea el estado inicial en memoria para pruebas stateful de onboarding.
 */
export function createOnboardingState(
  repoPath: string,
  localBranches: string[],
): OnboardingMockState {
  return {
    repoPath,
    localBranches,
    conexiones: [],
    mapaRamas: null,
    documentos: [],
    fabricaActiva: false,
    lineasActivadas: 0,
    auditEvents: [],
    mockVaultSecrets: {},
  };
}

/**
 * Instala handler Playwright stateful Cero-Nube sobre `/api/v1/**` para los 5 pasos
 * de onboarding, validando ramas contra `state.localBranches` y precondiciones de encendido.
 * Debe llamarse DESPUÉS de `setupApiMocks(page, ...)` para precederle vía `route.fallback()`.
 */
export async function setupOnboardingStatefulMocks(
  page: Page,
  state: OnboardingMockState,
): Promise<void> {
  let seq = 1;

  await page.route("**/api/v1/**", async (route) => {
    const reqUrl = new URL(route.request().url());
    const pathname = reqUrl.pathname;
    const method = route.request().method();

    // 1. Organización: refleja fabrica_activa y plan con 1 línea inicial
    if (pathname === "/api/v1/organization" && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "org_demo",
          nombre: "Demo Mobility",
          idioma: "es",
          timezone: "Europe/Madrid",
          despliegue: "saas",
          fabrica_activa: state.fabricaActiva,
          plan: {
            codigo: "starter",
            nombre: "Starter",
            lineas: 1,
            loc_mes: 10000,
            renueva_el: "2026-12-01",
          },
          polling: { notificaciones_s: 30, tablero_s: 60 },
        }),
      });
    }

    // 2. Dashboard Lines: líneas disponibles tras activación
    if (pathname === "/api/v1/dashboard/lines" && method === "GET") {
      const lineas = state.fabricaActiva
        ? Array.from({ length: state.lineasActivadas || 1 }, (_, i) => ({
            numero: i + 1,
            estado: "disponible",
            ticket_id: null,
            etapa: null,
          }))
        : [];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          lineas,
          en_espera: 0,
          contratadas: state.fabricaActiva ? state.lineasActivadas || 1 : 0,
        }),
      });
    }

    // 3. GET /api/v1/connections
    if (pathname === "/api/v1/connections" && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conexiones: state.conexiones,
          mapa_ramas: state.mapaRamas,
        }),
      });
    }

    // 4. POST /api/v1/connections
    if (pathname === "/api/v1/connections" && method === "POST") {
      const body = (route.request().postDataJSON() || {}) as {
        categoria: OnboardingConnectionItem["categoria"];
        proveedor: string;
        nombre?: string;
        config?: Record<string, unknown>;
        credenciales?: Record<string, unknown>;
      };
      const id = `cn_${seq++}`;
      const nowIso = new Date().toISOString();
      state.mockVaultSecrets[id] = body.credenciales ?? {};

      state.conexiones.push({
        id,
        categoria: body.categoria,
        proveedor: body.proveedor,
        nombre: body.nombre ?? null,
        estado: "conectada",
        ultimo_test: nowIso,
      });

      const needsWebhook = body.categoria === "tickets" || body.categoria === "cicd";
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id,
          estado: "conectada",
          permisos_verificados: ["repo:read", "repo:write"],
          ...(needsWebhook ? { webhook_token: `wh_mock_${id}` } : {}),
        }),
      });
    }

    // 5. POST /api/v1/connections/{id}/test
    const testMatch = pathname.match(/^\/api\/v1\/connections\/([^/]+)\/test$/);
    if (testMatch && method === "POST") {
      const connId = testMatch[1];
      const target = state.conexiones.find((c) => c.id === connId);
      const repoOk = fs.existsSync(path.join(state.repoPath, ".git"));
      if (target) {
        target.estado = repoOk ? "conectada" : "error";
        target.ultimo_test = new Date().toISOString();
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          estado: repoOk ? "conectada" : "error",
          detalle: { ok: repoOk, ramas_detectadas: state.localBranches },
        }),
      });
    }

    // 6. PUT /api/v1/connections/branch-map
    if (pathname === "/api/v1/connections/branch-map" && method === "PUT") {
      const body = (route.request().postDataJSON() || {}) as {
        feature: string;
        staging: string;
        produccion: string;
      };
      const missing: string[] = [];
      if (!state.localBranches.includes(body.staging)) {
        missing.push(body.staging);
      }
      if (!state.localBranches.includes(body.produccion)) {
        missing.push(body.produccion);
      }
      if (missing.length > 0) {
        return route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            error: {
              codigo: "RAMA_INEXISTENTE",
              mensaje: `Rama inexistente en el repositorio local: ${missing.join(", ")}.`,
              trace_id: "tr_mock_rama_422",
              timestamp: new Date().toISOString(),
            },
          }),
        });
      }

      state.mapaRamas = {
        feature: body.feature,
        staging: body.staging,
        produccion: body.produccion,
      };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ mapa_ramas: state.mapaRamas }),
      });
    }

    // 7. POST /api/v1/onboarding/documents
    if (pathname === "/api/v1/onboarding/documents" && method === "POST") {
      const body = (route.request().postDataJSON() || {}) as {
        nombre_archivo: string;
        tipo_documento?: string;
      };
      const did = `doc_${seq++}`;
      state.documentos.push({
        id: did,
        nombre_archivo: body.nombre_archivo || "docs/arquitectura.md",
        tipo_documento: body.tipo_documento || "spec",
      });
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          documento_id: did,
          upload_url: `http://localhost:10000/kallicode-local/${did}`,
          expira_en: new Date(Date.now() + 3600_000).toISOString(),
        }),
      });
    }

    // 8. GET /api/v1/onboarding/codemapping/status
    if (pathname === "/api/v1/onboarding/codemapping/status" && method === "GET") {
      const hasRepo = state.conexiones.some(
        (c) => c.categoria === "repositorio" && c.estado === "conectada",
      );
      const docsCount = state.documentos.length;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          fase: hasRepo ? 7 : 4,
          progreso: hasRepo ? 1.0 : 0.4,
          metricas: {
            definiciones: hasRepo ? 12 : 0,
            documentos: docsCount,
          },
          curacion_pendiente: 0,
          ultimo_refresh: new Date().toISOString(),
        }),
      });
    }

    // 9. POST /api/v1/onboarding/complete
    if (pathname === "/api/v1/onboarding/complete" && method === "POST") {
      const pendientes: string[] = [];
      const hasRepo = state.conexiones.some(
        (c) => c.categoria === "repositorio" && c.estado === "conectada",
      );
      if (!hasRepo) {
        pendientes.push("repositorio");
      }
      if (!state.mapaRamas) {
        pendientes.push("mapa_ramas");
      }
      if (pendientes.length > 0) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: {
              codigo: "ONBOARDING_INCOMPLETO",
              mensaje: `Faltan pasos obligatorios para encender la fábrica: ${pendientes.join(", ")}.`,
              detalle: { pendientes },
              trace_id: "tr_mock_ob_409",
              timestamp: new Date().toISOString(),
            },
          }),
        });
      }

      state.fabricaActiva = true;
      state.lineasActivadas = 1;
      state.auditEvents.push({
        evento: "fabrica_activada",
        resumen: "Fábrica activada con 1 línea de producción",
        fecha: new Date().toISOString(),
      });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          activado: true,
          lineas_activadas: 1,
          pendientes: [],
        }),
      });
    }

    // 10. Audit events verification
    if (pathname === "/api/v1/audit" && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: state.auditEvents }),
      });
    }

    return route.fallback();
  });
}
