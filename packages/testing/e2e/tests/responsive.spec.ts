import { test, expect, type Page } from "@playwright/test";
import { loginAs, navigateSpa, setupApiMocks } from "../fixtures/sesiones";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite E2E de Pruebas Responsivas de UI (Desktop, Tablet, Mobile) - KC-QA-B5-09.
 *
 * Certifica que el Portal Cliente se adapte fluidamente a los 3 factores de forma canónicos:
 * 1. Desktop (1920x1080): Navegación lateral expandida y disposición completa del Kanban.
 * 2. Tablet (768x1024): Barra de navegación horizontal, colapso de .nav-sec y scroll interno encapsulado.
 * 3. Mobile (375x812): Apilamiento vertical, filtros fluidos y operabilidad del modal de firma de Gate 1.
 *
 * Cumple con:
 * - Aserción estricta de cero desbordamiento horizontal involuntario (scrollWidth <= innerWidth).
 * - Generación de reporte JSON determinista (artifacts/responsive-report.json).
 * - Validación cruzada con kallicode_testing.responsive_reporter (Python).
 */

const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts");
const REPORT_PATH = path.join(ARTIFACTS_DIR, "responsive-report.json");
const REPO_ROOT = path.resolve(__dirname, "../../../..");

const CANONICAL_VIEWPORTS = {
  desktop: { width: 1920, height: 1080, label: "desktop" as const },
  tablet: { width: 768, height: 1024, label: "tablet" as const },
  mobile: { width: 375, height: 812, label: "mobile" as const },
};

/**
 * Verifica si el documento raíz tiene desbordamiento horizontal involuntario.
 */
async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    return document.documentElement.scrollWidth > window.innerWidth;
  });
}

/**
 * Verifica si la barra de navegación lateral está colapsada a formato horizontal o compacto.
 * En tokens.css: nav.kc .nav-sec { display: none } a partir de max-width: 900px.
 */
async function isSidebarCollapsed(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    const navSec = document.querySelector("nav.kc .nav-sec");
    if (!navSec) return true;
    const style = window.getComputedStyle(navSec);
    return style.display === "none";
  });
}

// Acumulador de resultados de auditoría para generación del reporte consolidado
interface ViewportAuditItem {
  view_name: string;
  viewport_label: "desktop" | "tablet" | "mobile";
  width: int;
  height: int;
  has_horizontal_overflow: boolean;
  sidebar_collapsed: boolean;
  passed: boolean;
  error_message?: string | null;
}

type int = number;

const accumulatedResults: ViewportAuditItem[] = [];

test.describe("Pruebas Responsivas de UI (Desktop, Tablet, Mobile) - KC-QA-B5-09", () => {
  test.beforeAll(() => {
    fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
  });

  // ============================================================================
  // Prueba 1: Adaptabilidad y Disposición en Pantalla Ancha (Desktop 1920x1080)
  // ============================================================================
  test("Adaptabilidad y Disposición en Pantalla Ancha (Desktop 1920x1080)", async ({ page }) => {
    await page.setViewportSize(CANONICAL_VIEWPORTS.desktop);
    await loginAs(page, "owner");

    // 1. Validar que la vista de tablero Kanban esté visible
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    // 2. Verificar que el menú lateral esté expandido con sus secciones visibles
    const collapsed = await isSidebarCollapsed(page);
    expect(collapsed, "El menú lateral debería estar expandido en desktop").toBe(false);

    // 3. Validar presencia simultánea de las 6 columnas del pipeline Kanban
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-design")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-build")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-qa")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-security")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-deploy")).toBeVisible();

    // 4. Aserción de cero desbordamiento horizontal global
    const overflow = await hasHorizontalOverflow(page);
    expect(overflow, "No debe existir desbordamiento horizontal en desktop").toBe(false);

    accumulatedResults.push({
      view_name: "kanban",
      viewport_label: "desktop",
      width: CANONICAL_VIEWPORTS.desktop.width,
      height: CANONICAL_VIEWPORTS.desktop.height,
      has_horizontal_overflow: overflow,
      sidebar_collapsed: collapsed,
      passed: !overflow && !collapsed,
    });
  });

  // ============================================================================
  // Prueba 2: Adaptabilidad y Colapso de Navegación en Tableta (Tablet 768x1024)
  // ============================================================================
  test("Adaptabilidad y Colapso de Navegación en Tableta (Tablet 768x1024)", async ({ page }) => {
    await page.setViewportSize(CANONICAL_VIEWPORTS.tablet);
    await loginAs(page, "owner");

    // 1. Validar renderizado de tablero
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    // 2. Verificar colapso responsivo del menú lateral a barra superior
    const collapsed = await isSidebarCollapsed(page);
    expect(collapsed, "El menú lateral debe colapsar a barra horizontal en tableta").toBe(true);

    // 3. Validar que el contenedor del kanban tenga scroll interno (.kanban overflow-x: auto)
    const kanbanScrollable = await page.evaluate(() => {
      const el = document.querySelector(".kanban");
      if (!el) return false;
      const style = window.getComputedStyle(el);
      return style.overflowX === "auto" || style.overflowX === "scroll";
    });
    expect(kanbanScrollable, "El contenedor .kanban debe admitir scroll horizontal interno").toBe(true);

    // 4. Aserción de cero desbordamiento horizontal global del documento
    const overflow = await hasHorizontalOverflow(page);
    expect(overflow, "No debe existir desbordamiento horizontal global en tableta").toBe(false);

    accumulatedResults.push({
      view_name: "kanban",
      viewport_label: "tablet",
      width: CANONICAL_VIEWPORTS.tablet.width,
      height: CANONICAL_VIEWPORTS.tablet.height,
      has_horizontal_overflow: overflow,
      sidebar_collapsed: collapsed,
      passed: !overflow && collapsed,
    });
  });

  // ============================================================================
  // Prueba 3: Adaptabilidad Vertical y Firma de Gate 1 en Móvil (Mobile 375x812)
  // ============================================================================
  test("Adaptabilidad Vertical y Firma de Gate 1 en Móvil (Mobile 375x812)", async ({ page }) => {
    await page.setViewportSize(CANONICAL_VIEWPORTS.mobile);
    await loginAs(page, "approver");

    // 1. Navegar a ticket con Gate 1 pendiente (KC-1003) vía SPA
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();

    // 2. Validar que el menú esté colapsado
    const collapsed = await isSidebarCollapsed(page);
    expect(collapsed, "El menú debe estar colapsado en formato móvil").toBe(true);

    // 3. Localizar panel de Gate 1 e ingresar comentario de aprobación
    const panelGate = page.getByTestId("detalle-ticket.gate.panel");
    await expect(panelGate).toBeVisible();
    await page
      .getByTestId("detalle-ticket.gate.comentario")
      .fill("Aprobación responsiva en pantalla móvil 375x812. Layout validado.");

    // 4. Abrir modal de firma
    const btnAprobar = page.getByTestId("detalle-ticket.gate.aprobar");
    await expect(btnAprobar).toBeVisible();
    await btnAprobar.click();

    // 5. Validar que el diálogo modal se ajuste al ancho de pantalla
    const modalFirma = page.getByTestId("detalle-ticket.firma.dialogo");
    await expect(modalFirma).toBeVisible();

    const boundingBox = await modalFirma.boundingBox();
    expect(boundingBox, "El modal de firma debe tener dimensiones calculables").not.toBeNull();
    if (boundingBox) {
      // El modal no debe exceder el ancho de la ventana móvil (375px)
      expect(
        boundingBox.width,
        "El ancho del modal debe estar acotado al ancho de pantalla móvil"
      ).toBeLessThanOrEqual(375);
    }

    // 6. Validar ausencia de desbordamiento horizontal global durante el diálogo
    const overflowModal = await hasHorizontalOverflow(page);
    expect(overflowModal, "El modal no debe generar desbordamiento horizontal en móvil").toBe(false);

    // 7. Validar botón de confirmar en el modal y completar la firma
    const btnConfirmar = page.getByTestId("detalle-ticket.firma.confirmar");
    await expect(btnConfirmar).toBeVisible();
    await btnConfirmar.click();

    // 8. Confirmar resultado de firma
    await expect(page.getByTestId("detalle-ticket.firma.resultado")).toBeVisible();

    accumulatedResults.push({
      view_name: "detalle-ticket-gate1-modal",
      viewport_label: "mobile",
      width: CANONICAL_VIEWPORTS.mobile.width,
      height: CANONICAL_VIEWPORTS.mobile.height,
      has_horizontal_overflow: overflowModal,
      sidebar_collapsed: collapsed,
      passed: !overflowModal && collapsed,
    });
  });

  // ============================================================================
  // Prueba 4: Matriz de Cero Desbordamiento Horizontal en 6 Vistas Clave
  // ============================================================================
  test("Matriz de Cero Desbordamiento Horizontal en 6 Vistas Clave", async ({ page }) => {
    const vistasAutenticadas = [
      { ruta: "/", testId: "tablero.vista.raiz", nombre: "tablero" },
      { ruta: "/nuevo-ticket", testId: "nuevo-ticket.vista.raiz", nombre: "nuevo-ticket" },
      { ruta: "/tickets/KC-1003", testId: "detalle-ticket.vista.raiz", nombre: "detalle-ticket" },
      { ruta: "/aprobaciones", testId: "aprobaciones.vista.raiz", nombre: "aprobaciones" },
      { ruta: "/conexiones", testId: "conexiones.vista.raiz", nombre: "conexiones" },
    ];

    const viewports = [
      CANONICAL_VIEWPORTS.desktop,
      CANONICAL_VIEWPORTS.tablet,
      CANONICAL_VIEWPORTS.mobile,
    ];

    // 1. Auditar vista pública /login en los 3 factores de forma
    for (const vp of viewports) {
      await page.setViewportSize(vp);
      await setupApiMocks(page, "owner");
      await page.goto("/login");
      await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

      const overflow = await hasHorizontalOverflow(page);
      expect(
        overflow,
        `Desbordamiento horizontal detectado en /login con viewport ${vp.label}`
      ).toBe(false);

      accumulatedResults.push({
        view_name: "login",
        viewport_label: vp.label,
        width: vp.width,
        height: vp.height,
        has_horizontal_overflow: overflow,
        sidebar_collapsed: vp.label !== "desktop",
        passed: !overflow,
      });
    }

    // 2. Iniciar sesión para auditar vistas privadas
    await page.setViewportSize(CANONICAL_VIEWPORTS.desktop);
    await loginAs(page, "owner");

    // 3. Iterar cada vista protegida en cada viewport
    for (const vista of vistasAutenticadas) {
      for (const vp of viewports) {
        await page.setViewportSize(vp);
        await navigateSpa(page, vista.ruta);
        await expect(page.getByTestId(vista.testId)).toBeVisible();

        const overflow = await hasHorizontalOverflow(page);
        const collapsed = await isSidebarCollapsed(page);

        expect(
          overflow,
          `Desbordamiento horizontal en vista ${vista.nombre} con viewport ${vp.label}`
        ).toBe(false);

        accumulatedResults.push({
          view_name: vista.nombre,
          viewport_label: vp.label,
          width: vp.width,
          height: vp.height,
          has_horizontal_overflow: overflow,
          sidebar_collapsed: collapsed,
          passed: !overflow,
        });
      }
    }
  });

  // ============================================================================
  // Prueba 5: Exportación de Reporte responsive-report.json y Validación con Python
  // ============================================================================
  test("Exportación de Reporte responsive-report.json y Validación con Python", async () => {
    // 1. Guardar resultados crudos en archivo temporal para que ResponsiveReporter los procese
    const rawDataPath = path.join(ARTIFACTS_DIR, "raw-responsive-data.json");
    fs.writeFileSync(rawDataPath, JSON.stringify(accumulatedResults, null, 2), "utf-8");

    // 2. Procesar y exportar el reporte oficial utilizando el módulo Python ResponsiveReporter
    const script = `
from pathlib import Path
import json
from kallicode_testing.responsive_reporter import ResponsiveReporter

raw_path = Path("${rawDataPath}")
output_report = Path("${REPORT_PATH}")

raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
report = ResponsiveReporter.parse_playwright_results(raw_data)
ResponsiveReporter.export_report_json(report, output_report)
ResponsiveReporter.assert_no_responsive_failures(report)
print(f"Reporte generado con exito: {report.report_id}, total={report.total_scenarios}, fallidos={report.failed_scenarios}")
`;
    execFileSync("uv", ["run", "python", "-c", script], { cwd: REPO_ROOT });

    // 3. Validar existencia y consistencia del archivo de reporte JSON
    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const content = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));

    expect(content.report_id).toMatch(/^resp-/);
    expect(content.is_conforming).toBe(true);
    expect(content.failed_scenarios).toBe(0);
    expect(content.total_scenarios).toBeGreaterThanOrEqual(18); // Al menos 6 vistas x 3 viewports

    // 4. Ejecutar CLI de ResponsiveReporter con la bandera estricta --check-failures
    const cliOutput = execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "kallicode_testing.responsive_reporter",
        "--report-file",
        REPORT_PATH,
        "--check-failures",
      ],
      { cwd: REPO_ROOT, encoding: "utf-8" }
    );

    expect(cliOutput).toContain("Conforme: True");
    expect(cliOutput).toContain("Fallidos: 0");
  });
});
