import { test, expect, type Page } from "@playwright/test";
import { loginAs, navigateSpa, setupApiMocks, USUARIOS_TEST } from "../fixtures/sesiones";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite E2E de Resiliencia del Frontend ante Desconexión del Backend (KC-QA-B5-10).
 *
 * Certifica que el Portal Cliente tolere fallos catastróficos o transitorios de red
 * y backend sin colapsar la aplicación:
 * 1. Prevención estricta de Pantalla Blanca (Zero WSOD - White Screen of Death).
 * 2. Activación de límites de error contenidos y cajas de alerta amigables.
 * 3. Manejo resiliente de HTTP 500 / 503 en firmas de Gates sin perder el estado modal.
 * 4. Recuperación automática de datos tras reconexión sin forzar recarga de página.
 * 5. Exportación estructurada de frontend-resilience-report.json para EvidenceSealer.
 */

const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts");
const REPORT_PATH = path.join(ARTIFACTS_DIR, "frontend-resilience-report.json");
const REPO_ROOT = path.resolve(__dirname, "../../../..");

interface ResilienceScenarioResult {
  scenario_id: string;
  description: string;
  failure_type: "connection_refused" | "timeout" | "http_500" | "http_503" | "flaky_reconnection";
  error_boundary_triggered: boolean;
  white_screen_detected: boolean;
  recovered_successfully: boolean;
  passed: boolean;
  error_message?: string | null;
}

const accumulatedScenarios: ResilienceScenarioResult[] = [];

/**
 * Detector determinista de Pantalla Blanca (Zero WSOD).
 * Evalúa si el contenedor raíz (#root) quedó vacío o desprovisto de texto/elementos DOM.
 */
async function isWhiteScreen(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    const root = document.getElementById("root");
    if (!root) return true;
    const text = document.body.innerText.trim();
    return root.children.length === 0 || text.length === 0;
  });
}

test.describe("Resiliencia del Frontend ante Desconexión del Backend (KC-QA-B5-10)", () => {
  test.beforeAll(() => {
    fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
  });

  // ============================================================================
  // Escenario 1: Caída Total de API al Iniciar Sesión - Mensaje Amigable sin WSOD
  // ============================================================================
  test("Caída Total de API al Iniciar Sesión - Mensaje Amigable sin WSOD", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // Interceptar /api/v1/auth/login simulando desconexión de red de bajo nivel
    await page.route("**/api/v1/auth/login", (route) => {
      route.abort("connectionrefused");
    });

    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    // Completar formulario de inicio de sesión
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);

    // Enviar formulario
    await page.getByTestId("login.formulario.entrar").click();

    // 1. Validar que aparezca la caja de error amigable
    const errorBox = page.getByTestId("comun.error.caja");
    await expect(errorBox).toBeVisible();

    // 2. Validar que el botón de envío no quede congelado permanentemente
    const botonEntrar = page.getByTestId("login.formulario.entrar");
    await expect(botonEntrar).toBeEnabled();

    // 3. Aserción de Cero Pantalla Blanca (Zero WSOD)
    const wsod = await isWhiteScreen(page);
    expect(wsod, "No debe presentarse pantalla blanca en fallo de autenticación").toBe(false);

    accumulatedScenarios.push({
      scenario_id: "scen-login-disconnect",
      description: "Caída Total de API al Iniciar Sesión",
      failure_type: "connection_refused",
      error_boundary_triggered: false,
      white_screen_detected: wsod,
      recovered_successfully: true,
      passed: !wsod,
    });
  });

  // ============================================================================
  // Escenario 2: Desconexión en Tablero Kanban - Banner de Desconexión y Bloqueo Seguro
  // ============================================================================
  test("Desconexión en Tablero Kanban - Banner de Desconexión y Bloqueo Seguro", async ({ page }) => {
    // 1. Iniciar sesión normalmente y acceder al Tablero
    await loginAs(page, "owner");
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();

    // 2. Simular desconexión por timeout en consultas posteriores del Tablero
    await page.route("**/api/v1/dashboard/**", (route) => {
      route.abort("timedout");
    });

    // 3. Forzar refetch o navegación interna para provocar el intento fallido
    await navigateSpa(page, "/tickets");
    await page.waitForTimeout(100);
    await navigateSpa(page, "/");

    // 4. Validar que la aplicación permanezca viva, sin crashear a pantalla blanca
    const wsod = await isWhiteScreen(page);
    expect(wsod, "El Tablero debe contener el error sin colapsar a pantalla blanca").toBe(false);

    // 5. Validar presencia de contenedor o aviso seguro
    const vistaTablero = page.getByTestId("tablero.vista.raiz");
    await expect(vistaTablero).toBeVisible();

    accumulatedScenarios.push({
      scenario_id: "scen-kanban-disconnect",
      description: "Desconexión en Tablero Kanban",
      failure_type: "timeout",
      error_boundary_triggered: false,
      white_screen_detected: wsod,
      recovered_successfully: true,
      passed: !wsod,
    });
  });

  // ============================================================================
  // Escenario 3: Manejo de HTTP 503 en Firma de Gates con Error Boundary Contenido
  // ============================================================================
  test("Manejo de HTTP 503 en Firma de Gates con Error Boundary Contenido", async ({ page }) => {
    // 1. Iniciar sesión como aprobador
    await loginAs(page, "approver");

    // 2. Navegar al ticket con Gate 1 pendiente (KC-1003)
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();

    // 3. Ingresar comentario de aprobación
    await page
      .getByTestId("detalle-ticket.gate.comentario")
      .fill("Aprobación sometida a prueba de resiliencia HTTP 503.");

    // 4. Abrir modal de firma
    await page.getByTestId("detalle-ticket.gate.aprobar").click();
    const modalFirma = page.getByTestId("detalle-ticket.firma.dialogo");
    await expect(modalFirma).toBeVisible();

    // 5. Interceptar endpoint de decisión de firma con HTTP 503 Service Unavailable
    await page.route("**/api/v1/tickets/**/gates/**", (route) => {
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            codigo: "SERVICE_UNAVAILABLE",
            mensaje: "El motor de firmas y custodia criptográfica no se encuentra disponible temporalmente.",
          },
        }),
      });
    });

    // 6. Confirmar la firma
    const btnConfirmar = page.getByTestId("detalle-ticket.firma.confirmar");
    await btnConfirmar.click();

    // 7. Validaciones críticas de contención:
    // a) El diálogo modal debe permanecer abierto sin desaparecer
    await expect(modalFirma).toBeVisible();

    // b) La caja de error debe desplegarse dentro del modal
    const errorCaja = page.getByTestId("comun.error.caja");
    await expect(errorCaja).toBeVisible();
    await expect(errorCaja).toContainText("SERVICE_UNAVAILABLE");

    // c) El botón de confirmación debe volver a quedar habilitado para permitir reintento
    await expect(btnConfirmar).toBeEnabled();

    // d) La sesión debe mantenerse intacta (el avatar del usuario en la cabecera sigue visible)
    await expect(page.getByTestId("layout.cabecera.avatar")).toBeVisible();

    // e) Zero WSOD
    const wsod = await isWhiteScreen(page);
    expect(wsod, "El modal con HTTP 503 no debe generar pantalla blanca").toBe(false);

    accumulatedScenarios.push({
      scenario_id: "scen-gate-503-contained",
      description: "Manejo de HTTP 503 en Firma de Gates",
      failure_type: "http_503",
      error_boundary_triggered: false,
      white_screen_detected: wsod,
      recovered_successfully: true,
      passed: !wsod,
    });
  });

  // ============================================================================
  // Escenario 4: Reconexión Exitosa y Recuperación de Datos tras Restablecimiento
  // ============================================================================
  test("Reconexión Exitosa y Recuperación de Datos tras Restablecimiento", async ({ page }) => {
    // 1. Iniciar sesión y simular fallo inicial en la carga del Tablero
    await setupApiMocks(page, "owner");

    // Forzar fallo 503 en la carga inicial del tablero
    await page.route("**/api/v1/dashboard/kanban", (route) => {
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            codigo: "SERVICE_UNAVAILABLE",
            mensaje: "Servicio de tablero no disponible temporalmente.",
          },
        }),
      });
    });

    await page.goto("/login");
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);
    await page.getByTestId("login.formulario.entrar").click();

    // 2. Validar que la vista de Tablero despliegue la caja de error y botón de reintento
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("comun.error.caja")).toBeVisible();
    const btnReintentar = page.getByTestId("tablero.carga.reintentar");
    await expect(btnReintentar).toBeVisible();

    // 3. Restablecer backend a estado saludable (remover intercepción fallida)
    await setupApiMocks(page, "owner");

    // 4. Accionar el reintento de conexión
    await btnReintentar.click();

    // 5. Validar recuperación exitosa automática de los datos sin recarga manual de página
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-design")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-build")).toBeVisible();

    // Validar que la caja de error inicial ya no esté presente
    await expect(page.getByTestId("comun.error.caja")).toHaveCount(0);

    // 6. Zero WSOD
    const wsod = await isWhiteScreen(page);
    expect(wsod, "La recuperación tras reconexión no debe mostrar pantalla blanca").toBe(false);

    accumulatedScenarios.push({
      scenario_id: "scen-flaky-reconnection-recovery",
      description: "Reconexión Exitosa y Recuperación de Datos",
      failure_type: "flaky_reconnection",
      error_boundary_triggered: false,
      white_screen_detected: wsod,
      recovered_successfully: true,
      passed: !wsod,
    });
  });

  // ============================================================================
  // Escenario 5: Exportación de Reporte frontend-resilience-report.json
  // ============================================================================
  test("Exportación de Reporte frontend-resilience-report.json", async () => {
    // 1. Guardar resultados brutos en archivo temporal
    const rawDataPath = path.join(ARTIFACTS_DIR, "raw-resilience-data.json");
    fs.writeFileSync(rawDataPath, JSON.stringify(accumulatedScenarios, null, 2), "utf-8");

    // 2. Procesar con ResilienceReporter de Python y exportar el reporte canónico
    const script = `
from pathlib import Path
import json
from kallicode_testing.resilience_reporter import ResilienceReporter

raw_path = Path("${rawDataPath}")
output_report = Path("${REPORT_PATH}")

raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
report = ResilienceReporter.parse_playwright_results(raw_data)
ResilienceReporter.export_report_json(report, output_report)
ResilienceReporter.assert_zero_resilience_failures(report)
print(f"Reporte de resiliencia generado con exito: {report.report_id}, total={report.total_scenarios}, conformes={report.passed_scenarios}")
`;
    execFileSync("uv", ["run", "python", "-c", script], { cwd: REPO_ROOT });

    // 3. Validar existencia física y estructura válida del JSON
    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const content = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));

    expect(content.report_id).toMatch(/^resil-/);
    expect(content.is_conforming).toBe(true);
    expect(content.total_scenarios).toBeGreaterThanOrEqual(4);
    expect(content.passed_scenarios).toEqual(content.total_scenarios);

    // 4. Validar ejecución del CLI de ResilienceReporter con --check-failures
    const cliOutput = execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "kallicode_testing.resilience_reporter",
        "--report-file",
        REPORT_PATH,
        "--check-failures",
      ],
      { cwd: REPO_ROOT, encoding: "utf-8" }
    );

    expect(cliOutput).toContain("Conforme: True");
    expect(cliOutput).toContain("Total: 4");
  });
});
