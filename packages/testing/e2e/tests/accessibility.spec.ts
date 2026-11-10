import { test, expect, loginAs, navigateSpa, setupApiMocks } from "../fixtures/sesiones";
import AxeBuilder from "@axe-core/playwright";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite Automatizada de Accesibilidad Web (WCAG 2.1 AA) - KC-QA-B5-03.
 *
 * Certifica que todas las vistas críticas del Portal Cliente cumplan estrictamente
 * con WCAG 2.1 Nivel A y AA, garantizando cero violaciones críticas o serias.
 */

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts");
const REPORT_PATH = path.join(ARTIFACTS_DIR, "axe-report.json");

/**
 * Filtra violaciones de impacto 'critical' o 'serious'.
 */
function extractCriticalOrSeriousViolations(violations: Array<{ id: string; impact?: string | null; help: string }>) {
  return violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious"
  );
}

test.describe("Auditoría de Accesibilidad Web WCAG 2.1 AA (KC-QA-B5-03)", () => {
  // ============================================================================
  // Prueba 1: Vista Login y Públicas
  // ============================================================================
  test("Auditoría WCAG 2.1 AA en Vista Login y Públicas", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // 1. Navegar a /login
    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    // 2. Ejecutar análisis Axe sobre #root
    const axeResults = await new AxeBuilder({ page })
      .include("#root")
      .withTags(WCAG_TAGS)
      .analyze();

    // 3. Aserción estricta de cero violaciones críticas o serias
    const fallosBloqueantes = extractCriticalOrSeriousViolations(axeResults.violations);
    expect(
      fallosBloqueantes,
      `Se detectaron violaciones críticas o serias de accesibilidad en /login: ${JSON.stringify(fallosBloqueantes, null, 2)}`
    ).toEqual([]);
  });

  // ============================================================================
  // Prueba 2: Tablero Kanban e Inspección de Tarjetas
  // ============================================================================
  test("Auditoría WCAG 2.1 AA en Tablero Kanban e Inspección de Tarjetas", async ({ page }) => {
    // 1. Iniciar sesión y confirmar Tablero principal
    await loginAs(page, "owner");
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();

    // 2. Ejecutar análisis Axe sobre main / #root
    const axeResults = await new AxeBuilder({ page })
      .include("#root")
      .withTags(WCAG_TAGS)
      .analyze();

    // 3. Aserción de cero violaciones críticas o serias
    const fallosBloqueantes = extractCriticalOrSeriousViolations(axeResults.violations);
    expect(
      fallosBloqueantes,
      `Se detectaron violaciones críticas o serias en el Tablero Kanban: ${JSON.stringify(fallosBloqueantes, null, 2)}`
    ).toEqual([]);
  });

  // ============================================================================
  // Prueba 3: Formulario de Alta de Tickets
  // ============================================================================
  test("Auditoría WCAG 2.1 AA en Formulario de Alta de Tickets", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Navegar al formulario de nuevo ticket vía SPA
    await navigateSpa(page, "/nuevo-ticket");
    await expect(page.getByTestId("nuevo-ticket.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("nuevo-ticket.formulario.titulo")).toBeVisible();

    // 2. Ejecutar análisis Axe sobre #root
    const axeResults = await new AxeBuilder({ page })
      .include("#root")
      .withTags(WCAG_TAGS)
      .analyze();

    // 3. Aserción de cero violaciones críticas o serias
    const fallosBloqueantes = extractCriticalOrSeriousViolations(axeResults.violations);
    expect(
      fallosBloqueantes,
      `Se detectaron violaciones críticas o serias en Formulario Nuevo Ticket: ${JSON.stringify(fallosBloqueantes, null, 2)}`
    ).toEqual([]);
  });

  // ============================================================================
  // Prueba 4: Detalle de Ticket y Pestañas
  // ============================================================================
  test("Auditoría WCAG 2.1 AA en Detalle de Ticket y Pestañas", async ({ page }) => {
    await loginAs(page, "approver");

    // 1. Navegar al ticket con Gate 1 pendiente (KC-1003) vía SPA
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("detalle-ticket.gate.panel")).toBeVisible();

    // 2. Ejecutar análisis Axe sobre #root
    const axeResults = await new AxeBuilder({ page })
      .include("#root")
      .withTags(WCAG_TAGS)
      .analyze();

    // 3. Aserción de cero violaciones críticas o serias
    const fallosBloqueantes = extractCriticalOrSeriousViolations(axeResults.violations);
    expect(
      fallosBloqueantes,
      `Se detectaron violaciones críticas o serias en Detalle de Ticket: ${JSON.stringify(fallosBloqueantes, null, 2)}`
    ).toEqual([]);
  });

  // ============================================================================
  // Prueba 5: Generación de Reporte Axe JSON e Integración Python
  // ============================================================================
  test("Generación de Reporte Axe JSON e Integración Python", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // 1. Navegar a /login para escaneo canónico
    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    // 2. Ejecutar análisis Axe completo
    const axeResults = await new AxeBuilder({ page })
      .include("#root")
      .withTags(WCAG_TAGS)
      .analyze();

    // 3. Persistir reporte axe-report.json en artifacts
    if (!fs.existsSync(ARTIFACTS_DIR)) {
      fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
    }
    fs.writeFileSync(REPORT_PATH, JSON.stringify(axeResults, null, 2), "utf-8");

    // 4. Validar que el archivo exista en disco y sea JSON válido
    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const contenido = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));
    expect(contenido).toHaveProperty("url");
    expect(contenido).toHaveProperty("violations");
    expect(contenido).toHaveProperty("passes");
    expect(contenido).toHaveProperty("timestamp");
  });
});
