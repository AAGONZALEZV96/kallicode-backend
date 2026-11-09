import { test, expect, type Page } from "@playwright/test";
import { loginAs, navigateSpa, setupApiMocks } from "../fixtures/sesiones";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite Automatizada de Rendimiento y Core Web Vitals (KC-QA-B5-12).
 *
 * Certifica que las vistas críticas del Portal Cliente cumplan estrictamente
 * con los presupuestos de rendimiento en entorno local Cero-Nube:
 * - TTFB <= 600 ms
 * - FCP < 1.2 segundos (1200 ms) - Criterio duro
 * - LCP <= 2.0 segundos (2000 ms)
 * - CLS <= 0.1
 */

const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts");
const REPORT_PATH = path.join(ARTIFACTS_DIR, "performance-vitals-report.json");
const REPO_ROOT = path.resolve(__dirname, "../../../..");

interface WebVitalsResult {
  view_name: string;
  ttfb_ms: number;
  fcp_ms: number;
  lcp_ms: number;
  cls: number;
  dom_interactive_ms: number;
  passed: boolean;
  violations: string[];
}

const accumulatedMetrics: WebVitalsResult[] = [];

/**
 * Mide las métricas de Core Web Vitals mediante las Performance APIs nativas del navegador.
 */
async function collectWebVitals(page: Page, viewName: string): Promise<WebVitalsResult> {
  const metrics = await page.evaluate(async () => {
    // Breve pausa para estabilización de render y flush de métricas
    await new Promise((r) => setTimeout(r, 200));

    // 1. Navigation Timing (TTFB, DOM Interactive)
    const navEntries = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
    const nav = navEntries[0];
    const ttfb_ms = nav ? Math.max(0, nav.responseStart - nav.requestStart) : 0;
    const dom_interactive_ms = nav ? nav.domInteractive : 0;

    // 2. First Contentful Paint (FCP)
    const paintEntries = performance.getEntriesByType("paint");
    const fcpEntry = paintEntries.find((e) => e.name === "first-contentful-paint");
    const fcp_ms = fcpEntry ? fcpEntry.startTime : 0;

    // 3. Largest Contentful Paint (LCP)
    let lcp_ms = fcp_ms;
    try {
      const lcpEntries = performance.getEntriesByType("largest-contentful-paint");
      if (lcpEntries.length > 0) {
        lcp_ms = lcpEntries[lcpEntries.length - 1].startTime;
      }
    } catch {
      lcp_ms = fcp_ms;
    }

    // 4. Cumulative Layout Shift (CLS)
    let cls = 0;
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as any[]) {
          if (!entry.hadRecentInput) {
            cls += entry.value;
          }
        }
      });
      observer.observe({ type: "layout-shift", buffered: true });
      await new Promise((r) => setTimeout(r, 100));
      observer.disconnect();
    } catch {
      cls = 0;
    }

    return {
      ttfb_ms: Math.round(ttfb_ms * 100) / 100,
      fcp_ms: Math.round(fcp_ms * 100) / 100,
      lcp_ms: Math.round(Math.max(lcp_ms, fcp_ms) * 100) / 100,
      cls: Math.round(cls * 1000) / 1000,
      dom_interactive_ms: Math.round(dom_interactive_ms * 100) / 100,
    };
  });

  const violations: string[] = [];
  if (metrics.ttfb_ms > 600.0) {
    violations.push(`TTFB excesivo: ${metrics.ttfb_ms}ms > 600ms`);
  }
  if (metrics.fcp_ms >= 1200.0) {
    violations.push(`FCP excesivo: ${metrics.fcp_ms}ms >= 1200ms`);
  }
  if (metrics.lcp_ms > 2000.0) {
    violations.push(`LCP excesivo: ${metrics.lcp_ms}ms > 2000ms`);
  }
  if (metrics.cls > 0.1) {
    violations.push(`CLS excesivo: ${metrics.cls} > 0.1`);
  }

  return {
    view_name: viewName,
    ...metrics,
    passed: violations.length === 0,
    violations,
  };
}

test.describe("Rendimiento de Carga Inicial y Core Web Vitals (KC-QA-B5-12)", () => {
  test.beforeAll(() => {
    fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
  });

  // ============================================================================
  // Escenario 1: Rendimiento de Carga Inicial en Vista Login (FCP < 1.2s)
  // ============================================================================
  test("Rendimiento de Carga Inicial en Vista Login (FCP < 1.2s)", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // 1. Navegar a /login
    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    // 2. Medir Core Web Vitals
    const result = await collectWebVitals(page, "login");

    // 3. Aserciones duras de presupuesto
    expect(result.ttfb_ms, `TTFB debe ser <= 600ms en /login`).toBeLessThanOrEqual(600.0);
    expect(result.fcp_ms, `FCP debe ser < 1.2s (1200ms) en /login`).toBeLessThan(1200.0);
    expect(result.lcp_ms, `LCP debe ser <= 2.0s (2000ms) en /login`).toBeLessThanOrEqual(2000.0);
    expect(result.cls, `CLS debe ser <= 0.1 en /login`).toBeLessThanOrEqual(0.1);
    expect(result.passed, `Presupuesto de Web Vitals violado en /login: ${result.violations.join(", ")}`).toBe(true);

    accumulatedMetrics.push(result);
  });

  // ============================================================================
  // Escenario 2: Rendimiento de Carga Inicial en Tablero Kanban (LCP <= 2.0s, CLS <= 0.1)
  // ============================================================================
  test("Rendimiento de Carga Inicial en Tablero Kanban (LCP <= 2.0s, CLS <= 0.1)", async ({ page }) => {
    // 1. Iniciar sesión y esperar renderizado completo del Tablero Kanban
    await loginAs(page, "owner");
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-design")).toBeVisible();

    // 2. Medir Core Web Vitals sobre las 6 columnas y tarjetas renderizadas
    const result = await collectWebVitals(page, "dashboard_kanban");

    // 3. Aserciones de rendimiento y estabilidad visual
    expect(result.fcp_ms, `FCP debe ser < 1.2s en Tablero Kanban`).toBeLessThan(1200.0);
    expect(result.lcp_ms, `LCP debe ser <= 2.0s en Tablero Kanban`).toBeLessThanOrEqual(2000.0);
    expect(result.cls, `CLS debe ser <= 0.1 (estabilidad visual) en Tablero Kanban`).toBeLessThanOrEqual(0.1);
    expect(result.passed, `Presupuesto de Web Vitals violado en Tablero: ${result.violations.join(", ")}`).toBe(true);

    accumulatedMetrics.push(result);
  });

  // ============================================================================
  // Escenario 3: Rendimiento de Carga en Detalle de Ticket /tickets/KC-1003
  // ============================================================================
  test("Rendimiento de Carga en Detalle de Ticket /tickets/KC-1003", async ({ page }) => {
    await loginAs(page, "approver");

    // 1. Navegar a ticket complejo con Gates y pestañas
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("detalle-ticket.gate.panel")).toBeVisible();

    // 2. Medir Core Web Vitals en montaje de pestañas y modales
    const result = await collectWebVitals(page, "detalle_ticket_kc1003");

    // 3. Validar cumplimiento de presupuesto
    expect(result.fcp_ms, `FCP debe ser < 1.2s en Detalle de Ticket`).toBeLessThan(1200.0);
    expect(result.lcp_ms, `LCP debe ser <= 2.0s en Detalle de Ticket`).toBeLessThanOrEqual(2000.0);
    expect(result.cls, `CLS debe ser <= 0.1 en Detalle de Ticket`).toBeLessThanOrEqual(0.1);
    expect(result.passed, `Presupuesto violado en Detalle de Ticket: ${result.violations.join(", ")}`).toBe(true);

    accumulatedMetrics.push(result);
  });

  // ============================================================================
  // Escenario 4: Detección de Violación de Presupuesto ante Latencia Artificial
  // ============================================================================
  test("Detección de Violación de Presupuesto ante Latencia Artificial", async () => {
    // 1. Crear métrica sintética con latencia y desfases excesivos
    const syntheticFailingData = {
      view_name: "vista_sobrecargada_sintetica",
      ttfb_ms: 780.0, // > 600ms
      fcp_ms: 1450.0, // >= 1200ms
      lcp_ms: 2600.0, // > 2000ms
      cls: 0.18, // > 0.1
      dom_interactive_ms: 950.0,
    };

    // 2. Ejecutar validación con el motor Python PerformanceReporter
    const script = `
import json
from kallicode_testing.performance_reporter import PerformanceReporter

raw = json.loads('${JSON.stringify(syntheticFailingData)}')
metric = PerformanceReporter.validate_metric(raw)
assert metric.passed is False, "La metrica sobrecargada debio ser marcada como no pasada"
assert len(metric.violations) == 4, f"Se esperaban 4 violaciones, pero hubo {len(metric.violations)}"
print("Deteccion de latencia artificial exitosa")
`;
    const output = execFileSync("uv", ["run", "python", "-c", script], {
      cwd: REPO_ROOT,
      encoding: "utf-8",
    });

    expect(output).toContain("Deteccion de latencia artificial exitosa");
  });

  // ============================================================================
  // Escenario 5: Exportación de Reporte performance-vitals-report.json
  // ============================================================================
  test("Exportación de Reporte performance-vitals-report.json", async () => {
    // 1. Guardar resultados acumulados en disco
    const rawMetricsPath = path.join(ARTIFACTS_DIR, "raw-performance-metrics.json");
    fs.writeFileSync(rawMetricsPath, JSON.stringify(accumulatedMetrics, null, 2), "utf-8");

    // 2. Procesar con PerformanceReporter y exportar reporte canónico
    const script = `
from pathlib import Path
import json
from kallicode_testing.performance_reporter import PerformanceReporter

raw_path = Path("${rawMetricsPath}")
output_report = Path("${REPORT_PATH}")

raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
report = PerformanceReporter.parse_playwright_results(raw_data)
PerformanceReporter.export_report_json(report, output_report)
PerformanceReporter.assert_zero_budget_violations(report)
print(f"Reporte de rendimiento exportado con exito: {report.report_id}, vistas={report.total_views_tested}")
`;
    execFileSync("uv", ["run", "python", "-c", script], { cwd: REPO_ROOT });

    // 3. Validar existencia y consistencia del archivo JSON
    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const content = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));

    expect(content.report_id).toMatch(/^perf-/);
    expect(content.is_conforming).toBe(true);
    expect(content.total_views_tested).toBeGreaterThanOrEqual(3);

    // 4. Ejecutar CLI de PerformanceReporter con la bandera --check-budget
    const cliOutput = execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "kallicode_testing.performance_reporter",
        "--report-file",
        REPORT_PATH,
        "--check-budget",
      ],
      { cwd: REPO_ROOT, encoding: "utf-8" }
    );

    expect(cliOutput).toContain("Conforme: True");
  });
});
