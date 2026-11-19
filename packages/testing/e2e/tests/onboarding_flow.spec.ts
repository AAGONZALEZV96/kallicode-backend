import {
  cleanupSyntheticSampleRepo,
  createOnboardingState,
  initSyntheticSampleRepo,
  SAMPLE_REPO_HTTPS_URL,
  SAMPLE_REPO_PATH,
  setupOnboardingStatefulMocks,
} from "../fixtures/onboarding_mock";
import { expect, navigateSpa, setupApiMocks, test, USUARIOS_TEST } from "../fixtures/sesiones";

/**
 * Suite E2E de Onboarding y Conexión de Repositorios Sintéticos (KC-QA-B5-06).
 *
 * Cumplimiento arquitectónico:
 * - Cero-Nube: repositorio Git local real en `/tmp/sample-repo` (`main`, `develop`, `feature/demo`)
 *   con teardown garantizado en `afterEach` y URL sintética `https://git.local.kallicode.test/sample-repo`.
 * - Selectores canónicos `data-testid` de `contratos/selectores-portal-v1.json`.
 * - Controlador stateful para `/connections`, `/connections/branch-map`, `/onboarding/documents`,
 *   `/onboarding/codemapping/status` y `/onboarding/complete`.
 */

test.describe("Onboarding de 5 Pasos con Repositorio Sintético Local (KC-QA-B5-06)", () => {
  test.afterEach(() => {
    cleanupSyntheticSampleRepo(SAMPLE_REPO_PATH);
  });

  test("test_onboarding_completo_cliente_nuevo_enciende_fabrica", async ({ page }) => {
    const branches = initSyntheticSampleRepo(SAMPLE_REPO_PATH);
    expect(branches).toEqual(expect.arrayContaining(["main", "develop"]));

    const state = createOnboardingState(SAMPLE_REPO_PATH, branches);
    await setupApiMocks(page, "owner");
    await setupOnboardingStatefulMocks(page, state);

    // Inicio de sesión con rol Owner y navegación SPA hacia /onboarding
    await page.goto("/login");
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);
    await page.getByTestId("login.formulario.entrar").click();
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    await navigateSpa(page, "/onboarding?paso=1");
    await expect(page.getByTestId("onboarding.vista.raiz")).toBeVisible();

    // ------------------------------------------------------------------------
    // Paso 1: Conexión de Repositorio Local Sintético (respaldo en /tmp/sample-repo)
    // ------------------------------------------------------------------------
    await expect(page.getByTestId("onboarding.categorias.repositorio")).toBeVisible();
    await page
      .getByTestId("onboarding.categorias.repositorio")
      .getByTestId("onboarding.formulario.abrir")
      .click();

    await page.getByTestId("onboarding.formulario.repo-url").fill(SAMPLE_REPO_HTTPS_URL);
    await page.getByTestId("onboarding.formulario.token").fill("mock_synthetic_repo_token_local");
    await page.getByTestId("onboarding.formulario.enviar").click();

    const exitoRepo = page.getByTestId("onboarding.formulario.exito");
    await expect(exitoRepo).toBeVisible();
    await exitoRepo.locator("button").click();

    // Probar conexión contra el repo sintético local
    await page.getByTestId("onboarding.conexiones.probar").first().click();
    await expect(page.getByTestId("onboarding.conexiones.resultado").first()).toBeVisible();

    // Avanzar al Paso 2
    await page.getByTestId("onboarding.navegacion.siguiente").click();

    // ------------------------------------------------------------------------
    // Paso 2: Fuentes de Tickets (kallicode_help) y CI/CD Mock
    // ------------------------------------------------------------------------
    const secTickets = page.getByTestId("onboarding.categorias.tickets");
    await expect(secTickets).toBeVisible();
    await secTickets.getByTestId("onboarding.formulario.abrir").click();
    await page.getByTestId("onboarding.formulario.proveedor").selectOption("kallicode_help");
    await page.getByTestId("onboarding.formulario.url").fill("https://help.local.kallicode.test");
    await page.getByTestId("onboarding.formulario.api-token").fill("mock_help_api_token_local");
    await page.getByTestId("onboarding.formulario.enviar").click();
    await expect(page.getByTestId("onboarding.formulario.exito")).toBeVisible();
    await page.getByTestId("onboarding.formulario.exito").locator("button").click();

    const secCicd = page.getByTestId("onboarding.categorias.cicd");
    await secCicd.getByTestId("onboarding.formulario.abrir").click();
    await page.getByTestId("onboarding.formulario.proveedor").selectOption("github_actions");
    await page.getByTestId("onboarding.formulario.repo-url").fill(SAMPLE_REPO_HTTPS_URL);
    await page.getByTestId("onboarding.formulario.token").fill("mock_cicd_token_local");
    await page.getByTestId("onboarding.formulario.enviar").click();
    await expect(page.getByTestId("onboarding.formulario.exito")).toBeVisible();
    await page.getByTestId("onboarding.formulario.exito").locator("button").click();

    // Avanzar al Paso 3
    await page.getByTestId("onboarding.navegacion.siguiente").click();

    // ------------------------------------------------------------------------
    // Paso 3: Mapa de Ramas (feature/*, staging=develop, produccion=main)
    // ------------------------------------------------------------------------
    await page.getByTestId("onboarding.ramas.feature").fill("feature/*");
    await page.getByTestId("onboarding.ramas.staging").fill("develop");
    await page.getByTestId("onboarding.ramas.produccion").fill("main");
    await page.getByTestId("onboarding.ramas.guardar").click();

    await expect(page.getByTestId("onboarding.ramas.exito")).toContainText("✓");
    await expect(page.getByTestId("onboarding.resumen.ramas")).toContainText("develop");
    await expect(page.getByTestId("onboarding.resumen.ramas")).toContainText("main");

    // Avanzar al Paso 4
    await page.getByTestId("onboarding.navegacion.siguiente").click();

    // ------------------------------------------------------------------------
    // Paso 4: Datos y Documentación de Arquitectura
    // ------------------------------------------------------------------------
    await expect(page.getByTestId("onboarding.panel.contenido")).toBeVisible();
    const uploadStatus = await page.evaluate(async () => {
      const resp = await fetch("/api/v1/onboarding/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nombre_archivo: "docs/arquitectura.md",
          content_type: "text/markdown",
          tamano_bytes: 128,
          tipo_documento: "spec",
        }),
      });
      return resp.status;
    });
    expect(uploadStatus).toBe(201);

    // Avanzar al Paso 5
    await page.getByTestId("onboarding.navegacion.siguiente").click();

    // ------------------------------------------------------------------------
    // Paso 5: CodeMapping Status y Encendido de Primera Línea
    // ------------------------------------------------------------------------
    await expect(page.getByTestId("onboarding.grafo.barra")).toHaveAttribute("aria-valuenow", "100");
    await expect(page.getByTestId("onboarding.activacion.encender")).toBeEnabled();

    const completeResponsePromise = page.waitForResponse(
      (res) => res.url().includes("/api/v1/onboarding/complete") && res.request().method() === "POST",
    );
    await page.getByTestId("onboarding.activacion.encender").click();
    await expect(page.getByTestId("onboarding.encendido.dialogo")).toBeVisible();
    await page.getByTestId("onboarding.encendido.confirmar").click();

    const completeResponse = await completeResponsePromise;
    expect(completeResponse.status()).toBe(200);
    const completeBody = (await completeResponse.json()) as {
      activado: boolean;
      lineas_activadas: number;
    };
    expect(completeBody.activado).toBe(true);
    expect(completeBody.lineas_activadas).toBe(1);

    // Aserciones finales en UI, estado de organización y auditoría
    await expect(page.getByTestId("onboarding.activacion.resultado")).toBeVisible();
    await expect(page.getByTestId("onboarding.activacion.tablero")).toBeVisible();
    expect(state.fabricaActiva).toBe(true);
    expect(state.auditEvents.some((ev) => ev.evento === "fabrica_activada")).toBe(true);

    await page.getByTestId("onboarding.activacion.tablero").click();
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    const linesSummary = await page.evaluate(async () => {
      const resp = await fetch("/api/v1/dashboard/lines");
      return (await resp.json()) as { lineas: Array<{ numero: number; estado: string }> };
    });
    expect(linesSummary.lineas).toHaveLength(1);
    expect(linesSummary.lineas[0]?.estado).toBe("disponible");
  });

  test("test_onboarding_bloquea_encendido_si_faltan_precondiciones", async ({ page }) => {
    const branches = initSyntheticSampleRepo(SAMPLE_REPO_PATH);
    const state = createOnboardingState(SAMPLE_REPO_PATH, branches);
    await setupApiMocks(page, "owner");
    await setupOnboardingStatefulMocks(page, state);

    await page.goto("/login");
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);
    await page.getByTestId("login.formulario.entrar").click();
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    await navigateSpa(page, "/onboarding?paso=1");
    await expect(page.getByTestId("onboarding.vista.raiz")).toBeVisible();

    // Conectar solo el repositorio (Paso 1), omitiendo Mapa de Ramas (Paso 3)
    await page
      .getByTestId("onboarding.categorias.repositorio")
      .getByTestId("onboarding.formulario.abrir")
      .click();
    await page.getByTestId("onboarding.formulario.repo-url").fill(SAMPLE_REPO_HTTPS_URL);
    await page.getByTestId("onboarding.formulario.token").fill("mock_synthetic_repo_token_local");
    await page.getByTestId("onboarding.formulario.enviar").click();
    await expect(page.getByTestId("onboarding.formulario.exito")).toBeVisible();
    await page.getByTestId("onboarding.formulario.exito").locator("button").click();

    // Saltar directo al Paso 5 (Activación)
    await page.getByTestId("onboarding.pasos.paso").nth(4).click();

    const pendientes = page.getByTestId("onboarding.activacion.pendientes");
    await expect(pendientes).toBeVisible();
    await expect(page.getByTestId("onboarding.activacion.encender")).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    // Forzar invocación directa POST /onboarding/complete -> HTTP 409 ONBOARDING_INCOMPLETO
    const forcedResult = await page.evaluate(async () => {
      const resp = await fetch("/api/v1/onboarding/complete", { method: "POST" });
      return {
        status: resp.status,
        body: (await resp.json()) as {
          error: { codigo: string; mensaje: string; detalle?: { pendientes: string[] } };
        },
      };
    });
    expect(forcedResult.status).toBe(409);
    expect(forcedResult.body.error.codigo).toBe("ONBOARDING_INCOMPLETO");
    expect(forcedResult.body.error.detalle?.pendientes).toContain("mapa_ramas");
    expect(state.fabricaActiva).toBe(false);
  });

  test("test_onboarding_validacion_rama_inexistente", async ({ page }) => {
    const branches = initSyntheticSampleRepo(SAMPLE_REPO_PATH);
    const state = createOnboardingState(SAMPLE_REPO_PATH, branches);
    await setupApiMocks(page, "owner");
    await setupOnboardingStatefulMocks(page, state);

    await page.goto("/login");
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);
    await page.getByTestId("login.formulario.entrar").click();
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();

    await navigateSpa(page, "/onboarding?paso=3");
    await expect(page.getByTestId("onboarding.vista.raiz")).toBeVisible();

    await page.getByTestId("onboarding.ramas.feature").fill("feature/*");
    await page.getByTestId("onboarding.ramas.staging").fill("develop");
    await page.getByTestId("onboarding.ramas.produccion").fill("rama_fantasma_404");

    const branchResponsePromise = page.waitForResponse(
      (res) =>
        res.url().includes("/api/v1/connections/branch-map") && res.request().method() === "PUT",
    );
    await page.getByTestId("onboarding.ramas.guardar").click();

    const branchResponse = await branchResponsePromise;
    expect(branchResponse.status()).toBe(422);
    const errorPayload = (await branchResponse.json()) as {
      error: { codigo: string; mensaje: string };
    };
    expect(errorPayload.error.codigo).toBe("RAMA_INEXISTENTE");

    // Verificar rechazo visible en UI vía data-testid oficial y que no se guardó el mapa
    const branchError = page.getByTestId("onboarding.ramas.error").first();
    await expect(branchError).toBeVisible();
    await expect(branchError).toContainText("rama_fantasma_404");
    await expect(page.getByTestId("onboarding.ramas.produccion")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(state.mapaRamas).toBeNull();
  });
});
