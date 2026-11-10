import { test, expect, loginAs, setupApiMocks } from "../fixtures/sesiones";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * Suite E2E de Internacionalización (i18n) del Portal Cliente (KC-QA-B5-07).
 *
 * Certifica que la aplicación responda de manera reactiva e inmediata a cambios
 * de idioma (Español <-> Inglés), persista la preferencia en LocalStorage y no
 * exponga claves desnudas ni marcadores sin traducir en la interfaz.
 */

const ARTIFACTS_DIR = path.resolve(__dirname, "../artifacts");
const REPORT_PATH = path.join(ARTIFACTS_DIR, "i18n-report.json");
const REPO_ROOT = path.resolve(__dirname, "../../../..");

test.describe("Internacionalización (i18n) del Portal Cliente (KC-QA-B5-07)", () => {
  test.beforeAll(() => {
    fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
  });

  // ============================================================================
  // Prueba 1: Conmutación Dinámica de Idioma Español <-> Inglés en UI
  // ============================================================================
  test("Conmutación Dinámica de Idioma Español <-> Inglés en UI", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Validar estado inicial en Español
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(page.getByTestId("layout.cabecera.idioma")).toHaveValue("es");
    await expect(page.getByTestId("layout.cabecera.salir")).toHaveText("Salir");
    await expect(page.getByTestId("layout.nav.tablero")).toContainText("Tablero");

    // 2. Conmutar dinámicamente a Inglés ('en')
    await page.getByTestId("layout.cabecera.idioma").selectOption("en");

    // 3. Validar actualización inmediata sin recargar página
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByTestId("layout.cabecera.idioma")).toHaveValue("en");
    await expect(page.getByTestId("layout.cabecera.salir")).toHaveText("Sign out");
    await expect(page.getByTestId("layout.nav.tablero")).toContainText("Dashboard");

    // 4. Conmutar nuevamente a Español ('es')
    await page.getByTestId("layout.cabecera.idioma").selectOption("es");

    // 5. Validar retorno inmediato al idioma base
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(page.getByTestId("layout.cabecera.idioma")).toHaveValue("es");
    await expect(page.getByTestId("layout.cabecera.salir")).toHaveText("Salir");
    await expect(page.getByTestId("layout.nav.tablero")).toContainText("Tablero");
  });

  // ============================================================================
  // Prueba 2: Ausencia de Claves Desnudas o Fallbacks Raw en Renderizado
  // ============================================================================
  test("Ausencia de Claves Desnudas o Fallbacks Raw en Renderizado", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // Evaluar primero en la vista pública de login
    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    const bodyTextLogin = await page.innerText("body");
    expect(bodyTextLogin).not.toContain("missing_translation");
    expect(bodyTextLogin).not.toMatch(/\b(login|nav|hdr|dash|st|tk|nt|nf|td|firma|err|rol|sub)\.[a-z0-9_.-]+\b/);

    // Evaluar en el dashboard autenticado
    await loginAs(page, "owner");
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    // Comprobar en Español
    const bodyTextDashboardEs = await page.innerText("body");
    expect(bodyTextDashboardEs).not.toContain("missing_translation");
    expect(bodyTextDashboardEs).not.toMatch(/\b(nav|hdr|dash|st|tk|nt|nf|td|firma|err|rol|sub)\.[a-z0-9_.-]+\b/);

    // Conmutar a Inglés y comprobar nuevamente
    await page.getByTestId("layout.cabecera.idioma").selectOption("en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    const bodyTextDashboardEn = await page.innerText("body");
    expect(bodyTextDashboardEn).not.toContain("missing_translation");
    expect(bodyTextDashboardEn).not.toMatch(/\b(nav|hdr|dash|st|tk|nt|nf|td|firma|err|rol|sub)\.[a-z0-9_.-]+\b/);
  });

  // ============================================================================
  // Prueba 3: Persistencia del Locale Seleccionado en LocalStorage
  // ============================================================================
  test("Persistencia del Locale Seleccionado en LocalStorage", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Cambiar a idioma Inglés
    await page.getByTestId("layout.cabecera.idioma").selectOption("en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    // 2. Verificar que se haya almacenado en localStorage con la clave 'kc_idioma'
    const storedLang = await page.evaluate(() => localStorage.getItem("kc_idioma"));
    expect(storedLang).toBe("en");

    // 3. Recargar la página completamente
    await page.reload();

    // 4. Certificar que el portal conserve el idioma inglés tras la recarga (en Login público)
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByTestId("login.formulario.entrar")).toHaveText("Sign in");

    // 5. Iniciar sesión nuevamente: la sesión hereda la preferencia persistida
    await loginAs(page, "owner");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByTestId("layout.cabecera.idioma")).toHaveValue("en");
    await expect(page.getByTestId("layout.cabecera.salir")).toHaveText("Sign out");
    await expect(page.getByTestId("layout.nav.tablero")).toContainText("Dashboard");

    // Restaurar a español
    await page.getByTestId("layout.cabecera.idioma").selectOption("es");
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
  });

  // ============================================================================
  // Prueba 4: Exportación de Reporte i18n-report.json e Integración WORM
  // ============================================================================
  test("Exportación de Reporte i18n-report.json e Integración WORM", async () => {
    const esPath = path.resolve(__dirname, "../../tests/fixtures/locales/es.json");
    const enPath = path.resolve(__dirname, "../../tests/fixtures/locales/en.json");

    // Ejecutar I18nValidator exportando el reporte canónico
    const script = `
from pathlib import Path
from kallicode_testing.i18n_validator import I18nValidator

validator = I18nValidator()
report = validator.compare_locale_files("${esPath}", "${enPath}")
validator.export_report_json(report, "${REPORT_PATH}")
validator.assert_full_i18n_parity(report)
`;
    execFileSync("uv", ["run", "python", "-c", script], { cwd: REPO_ROOT });

    expect(fs.existsSync(REPORT_PATH)).toBe(true);
    const content = JSON.parse(fs.readFileSync(REPORT_PATH, "utf-8"));

    expect(content.report_id).toMatch(/^i18n-/);
    expect(content.locales_analyzed).toEqual(["es", "en"]);
    expect(content.is_conforming).toBe(true);
    expect(content.missing_keys_count).toBe(0);
    expect(content.mismatches).toHaveLength(0);
    expect(content.total_keys).toBeGreaterThanOrEqual(200);
  });
});
