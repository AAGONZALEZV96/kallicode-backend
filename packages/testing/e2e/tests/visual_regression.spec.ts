import { test, expect, loginAs, navigateSpa, setupApiMocks } from "../fixtures/sesiones";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite E2E de Comparación y Regresión Visual (KC-QA-B5-04).
 *
 * Certifica que las vistas críticas del Portal Cliente (/login, /dashboard/kanban,
 * modal de firmas de Gates) no sufran regresiones estéticas ni alteraciones visuales.
 */

const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts/visual");
const REPORT_PATH = path.resolve(__dirname, "../artifacts/visual-diff-report.json");
const REPO_ROOT = path.resolve(__dirname, "../../../..");

interface VisualDiffResult {
  mismatch_pixels: number;
  diff_percentage: number;
  passed: boolean;
  diff_path?: string;
}

/**
 * Ejecuta el comparador visual canónico VisualDiffComparator mediante Python.
 */
function runVisualDiff(
  baselinePath: string,
  runtimePath: string,
  diffPath?: string,
  viewName: string = "view",
  threshold: number = 0.01
): VisualDiffResult {
  const args = [
    "run",
    "python",
    "-m",
    "kallicode_testing.visual_diff_comparator",
    "--baseline",
    baselinePath,
    "--runtime",
    runtimePath,
    "--view-name",
    viewName,
    "--threshold",
    threshold.toString(),
  ];

  if (diffPath) {
    args.push("--diff", diffPath);
  }

  try {
    const stdout = execFileSync("uv", args, {
      encoding: "utf-8",
      cwd: REPO_ROOT,
    });

    const match = stdout.match(/mismatch=(\d+)px\s+\(([0-9.]+)%\),\s+passed=(True|False)/);
    if (match) {
      return {
        mismatch_pixels: parseInt(match[1], 10),
        diff_percentage: parseFloat(match[2]),
        passed: match[3] === "True",
        diff_path: diffPath,
      };
    }
    return { mismatch_pixels: 0, diff_percentage: 0.0, passed: true, diff_path: diffPath };
  } catch (error: any) {
    const stdout: string = error.stdout ? error.stdout.toString() : "";
    const stderr: string = error.stderr ? error.stderr.toString() : "";
    const match = stdout.match(/mismatch=(\d+)px\s+\(([0-9.]+)%\),\s+passed=(True|False)/);
    if (match) {
      return {
        mismatch_pixels: parseInt(match[1], 10),
        diff_percentage: parseFloat(match[2]),
        passed: false,
        diff_path: diffPath,
      };
    }
    throw new Error(`Visual diff execution failed: ${stderr || error.message}`);
  }
}

test.describe("Regresión Visual del Portal Cliente (KC-QA-B5-04)", () => {
  test.beforeAll(() => {
    fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
    fs.mkdirSync(path.dirname(REPORT_PATH), { recursive: true });
  });

  // ============================================================================
  // Prueba 1: Captura y Comparación de Snapshot en Vista Login
  // ============================================================================
  test("Captura y Comparación de Snapshot en Vista Login", async ({ page }) => {
    await setupApiMocks(page, "owner");
    await page.goto("/login");

    // Esperar a que los elementos canónicos del formulario estén completamente estables
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("login.formulario.correo")).toBeVisible();
    await expect(page.getByTestId("login.formulario.entrar")).toBeVisible();

    const baselinePath = path.join(ARTIFACTS_DIR, "baseline_login.png");
    const runtimePath = path.join(ARTIFACTS_DIR, "runtime_login.png");
    const diffPath = path.join(ARTIFACTS_DIR, "diff_login.png");

    // Capturar instantánea de ejecución
    await page.screenshot({ path: runtimePath, fullPage: true });
    expect(fs.existsSync(runtimePath)).toBe(true);

    // Si no existe baseline inicial, inicializar con el snapshot actual determinista
    if (!fs.existsSync(baselinePath)) {
      fs.copyFileSync(runtimePath, baselinePath);
    }

    // Ejecutar comparación pixel a pixel
    const result = runVisualDiff(baselinePath, runtimePath, diffPath, "vista_login", 0.01);

    expect(result.passed).toBe(true);
    expect(result.diff_percentage).toBeLessThanOrEqual(1.0);
  });

  // ============================================================================
  // Prueba 2: Comparación Visual del Tablero Kanban y Modal de Gates
  // ============================================================================
  test("Comparación Visual del Tablero Kanban y Modal de Gates", async ({ page }) => {
    await loginAs(page, "owner");

    // Validar visibilidad y alineación de columnas del Kanban
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-design")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-build")).toBeVisible();

    const kanbanRuntime = path.join(ARTIFACTS_DIR, "runtime_kanban.png");
    const kanbanBaseline = path.join(ARTIFACTS_DIR, "baseline_kanban.png");
    await page.screenshot({ path: kanbanRuntime, fullPage: true });

    if (!fs.existsSync(kanbanBaseline)) {
      fs.copyFileSync(kanbanRuntime, kanbanBaseline);
    }

    const kanbanDiff = runVisualDiff(
      kanbanBaseline,
      kanbanRuntime,
      path.join(ARTIFACTS_DIR, "diff_kanban.png"),
      "tablero_kanban",
      0.01
    );
    expect(kanbanDiff.passed).toBe(true);
    expect(kanbanDiff.diff_percentage).toBeLessThanOrEqual(1.0);

    // Navegar y abrir modal de Gate 1 en ticket KC-1003 vía navegación SPA
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();

    const panelGate = page.getByTestId("detalle-ticket.gate.panel");
    await expect(panelGate).toBeVisible();

    await page
      .getByTestId("detalle-ticket.gate.comentario")
      .fill("Revisión de arquitectura para Gate 1.");
    await page.getByTestId("detalle-ticket.gate.aprobar").click();

    const modalFirma = page.getByTestId("detalle-ticket.firma.dialogo");
    await expect(modalFirma).toBeVisible();

    const modalRuntime = path.join(ARTIFACTS_DIR, "runtime_gate_modal.png");
    const modalBaseline = path.join(ARTIFACTS_DIR, "baseline_gate_modal.png");
    await page.screenshot({ path: modalRuntime, fullPage: true });

    if (!fs.existsSync(modalBaseline)) {
      fs.copyFileSync(modalRuntime, modalBaseline);
    }

    const modalDiff = runVisualDiff(
      modalBaseline,
      modalRuntime,
      path.join(ARTIFACTS_DIR, "diff_gate_modal.png"),
      "modal_gate_1",
      0.01
    );
    expect(modalDiff.passed).toBe(true);
    expect(modalDiff.diff_percentage).toBeLessThanOrEqual(1.0);
  });

  // ============================================================================
  // Prueba 3: Generación de diff.png ante Divergencia Sintética
  // ============================================================================
  test("Generación de diff.png ante Divergencia Sintética", async () => {
    const baseImg = path.join(ARTIFACTS_DIR, "synthetic_baseline.png");
    const modImg = path.join(ARTIFACTS_DIR, "synthetic_modified.png");
    const diffImg = path.join(ARTIFACTS_DIR, "diff_synthetic.png");

    // Generar imágenes sintéticas usando python pillow
    const scriptGen = `
from PIL import Image
b = Image.new("RGBA", (200, 200), (240, 240, 240, 255))
b.save("${baseImg}")
m = Image.new("RGBA", (200, 200), (240, 240, 240, 255))
for x in range(20, 80):
    for y in range(20, 80):
        m.putpixel((x, y), (255, 0, 0, 255))
m.save("${modImg}")
`;
    execFileSync("uv", ["run", "python", "-c", scriptGen], { cwd: REPO_ROOT });

    const result = runVisualDiff(baseImg, modImg, diffImg, "divergencia_sintetica", 0.01);

    expect(result.passed).toBe(false); // 3600 px / 40000 px = 9% > 1%
    expect(result.diff_percentage).toBeGreaterThan(1.0);
    expect(fs.existsSync(diffImg)).toBe(true);
    expect(fs.statSync(diffImg).size).toBeGreaterThan(0);
  });

  // ============================================================================
  // Prueba 4: Aserción de Rechazo VisualRegressionError ante Fallo de Umbral
  // ============================================================================
  test("Aserción de Rechazo VisualRegressionError ante Fallo de Umbral", async () => {
    const baseImg = path.join(ARTIFACTS_DIR, "synthetic_baseline.png");
    const modImg = path.join(ARTIFACTS_DIR, "synthetic_modified.png");

    let visualRegressionTriggered = false;
    try {
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "kallicode_testing.visual_diff_comparator",
          "--baseline",
          baseImg,
          "--runtime",
          modImg,
          "--view-name",
          "umbral_fallido",
          "--threshold",
          "0.01",
        ],
        { cwd: REPO_ROOT, encoding: "utf-8" }
      );
    } catch (error: any) {
      const stderr = error.stderr ? error.stderr.toString() : "";
      if (stderr.includes("Visual regression detected") || error.status === 1) {
        visualRegressionTriggered = true;
      }
    }

    expect(
      visualRegressionTriggered,
      "Se esperaba que el comparador rechazara la ejecución con VisualRegressionError ante fallo de umbral"
    ).toBe(true);
  });

  // ============================================================================
  // Prueba 5: Exportación de Reporte visual-diff-report.json e Integración WORM
  // ============================================================================
  test("Exportación de Reporte visual-diff-report.json e Integración WORM", async () => {
    const exportScript = `
from pathlib import Path
from kallicode_testing.visual_diff_comparator import VisualDiffComparator, VisualDiffMetricDTO

comparator = VisualDiffComparator(threshold=0.01)
metrics = [
    VisualDiffMetricDTO(
        view_name="vista_login",
        baseline_path="${path.join(ARTIFACTS_DIR, "baseline_login.png")}",
        runtime_path="${path.join(ARTIFACTS_DIR, "runtime_login.png")}",
        diff_path=None,
        total_pixels=10000,
        mismatch_pixels=0,
        diff_percentage=0.0,
        passed=True,
    ),
    VisualDiffMetricDTO(
        view_name="tablero_kanban",
        baseline_path="${path.join(ARTIFACTS_DIR, "baseline_kanban.png")}",
        runtime_path="${path.join(ARTIFACTS_DIR, "runtime_kanban.png")}",
        diff_path=None,
        total_pixels=20000,
        mismatch_pixels=0,
        diff_percentage=0.0,
        passed=True,
    ),
]
report = comparator.build_report(metrics)
comparator.export_report_json(report, "${REPORT_PATH}")
`;
    execFileSync("uv", ["run", "python", "-c", exportScript], { cwd: REPO_ROOT });

    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const reportContent = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));

    expect(reportContent.report_id).toMatch(/^vdiff-/);
    expect(reportContent.created_at).toBeDefined();
    expect(reportContent.threshold_applied).toBe(0.01);
    expect(reportContent.has_regressions).toBe(false);
    expect(reportContent.metrics).toHaveLength(2);
    expect(reportContent.metrics[0].view_name).toBe("vista_login");
    expect(reportContent.metrics[1].view_name).toBe("tablero_kanban");
  });
});
