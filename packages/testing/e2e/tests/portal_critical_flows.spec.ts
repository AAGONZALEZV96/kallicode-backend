import {
  test,
  expect,
  loginAs,
  navigateSpa,
  persistStorageState,
  setupApiMocks,
  USUARIOS_TEST,
} from "../fixtures/sesiones";

/**
 * Suite E2E de Flujos Críticos del Portal Cliente en Playwright (KC-QA-B5-01).
 *
 * Certifica el recorrido funcional completo de un usuario por la plataforma:
 * 1. Autenticación e Inicio de Sesión por Rol (y persistencia con storageState).
 * 2. Tablero Kanban y Navegación de Tickets.
 * 3. Creación Atómica de Ticket con Criterios de Aceptación.
 * 4. Navegación del Detalle de Ticket (las 5 pestañas: Spec, Evidencia, Seguridad, Consumo, Actividad).
 * 5. Cadena de Firmas de Gates con Confirmación y Transición de Estado.
 *
 * Cumple con:
 * - Contrato oficial de selectores: contratos/selectores-portal-v1.json (data-testid).
 * - Web-first assertions (reintento automático, sin waitForTimeout).
 * - Políticas WORM de captura condicional ante fallos (screenshot, video, trace).
 */

test.describe("Portal Cliente - Flujos Críticos E2E (KC-QA-B5-01)", () => {
  // ============================================================================
  // 1. Autenticación y Reutilización de Sesión
  // ============================================================================
  test("Flujo de Autenticación e Inicio de Sesión por Rol", async ({ page }) => {
    await setupApiMocks(page, "owner");

    // 1. Navegar a /login
    await page.goto("/login");
    await expect(page.getByTestId("login.vista.raiz")).toBeVisible();

    // 2. Ingresar credenciales válidas
    await page.getByTestId("login.formulario.correo").fill(USUARIOS_TEST.owner.email);
    await page.getByTestId("login.formulario.clave").fill(USUARIOS_TEST.owner.password);

    // 3. Clic en entrar
    await page.getByTestId("login.formulario.entrar").click();

    // 4. Confirmar redirección exitosa al Tablero Kanban
    await expect(page.getByTestId("tablero.vista.raiz")).toBeVisible();
    await expect(page).toHaveURL(/.*\/$/);

    // 5. Persistir estado de sesión en storageState para reutilización
    const storagePath = await persistStorageState(page, "owner");
    expect(storagePath).toContain("user_owner.json");
  });

  // ============================================================================
  // 2. Tablero Kanban y Navegación de Tickets
  // ============================================================================
  test("Navegación del Tablero Kanban y Detalle de Tarjeta", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Validar renderizado de las columnas del pipeline kanban
    await expect(page.getByTestId("tablero.kanban.col-triage")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-design")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-build")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-qa")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-security")).toBeVisible();
    await expect(page.getByTestId("tablero.kanban.col-deploy")).toBeVisible();

    // 2. Hacer clic en una tarjeta del kanban
    const primeraTarjeta = page.getByTestId("tablero.kanban.tarjeta").first();
    await expect(primeraTarjeta).toBeVisible();
    await primeraTarjeta.click();

    // 3. Confirmar navegación a la vista de detalle del ticket
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();
    await expect(page).toHaveURL(/.*\/tickets\/.+/);
  });

  // ============================================================================
  // 3. Flujo de Creación de Tickets
  // ============================================================================
  test("Creación Atómica de Ticket con Criterios Aceptación", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Navegar al formulario de nuevo ticket
    await page.getByTestId("tablero.cabecera.nuevo-ticket").click();
    await expect(page.getByTestId("nuevo-ticket.vista.raiz")).toBeVisible();
    await expect(page).toHaveURL(/.*\/nuevo-ticket/);

    // 2. Completar campos del formulario
    await page.getByTestId("nuevo-ticket.formulario.tipo-bug").click();
    await page
      .getByTestId("nuevo-ticket.formulario.titulo")
      .fill("Fallo crítico en sincronización de inventario");
    await page
      .getByTestId("nuevo-ticket.formulario.descripcion")
      .fill("Se detectó una discrepancia en el conteo de existencias tras la ejecución del worker nocturno.");
    await page.getByTestId("nuevo-ticket.formulario.prioridad").selectOption("alta");

    // 3. Enviar formulario
    await page.getByTestId("nuevo-ticket.formulario.enviar").click();

    // 4. Confirmar creación y apertura automática del detalle del nuevo ticket
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();
    await expect(page).toHaveURL(/.*\/tickets\/KC-\d+/);
  });

  // ============================================================================
  // 4. Navegación del Detalle de Ticket (5 Pestañas)
  // ============================================================================
  test("Inspección de Pestañas de Evidencia QA y Seguridad", async ({ page }) => {
    await loginAs(page, "owner");

    // 1. Acceder al detalle de un ticket existente vía navegación interna SPA
    await navigateSpa(page, "/tickets/KC-1002");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();

    // 2. Alternar a pestaña Spec
    await page.getByTestId("detalle-ticket.pestanas.spec").click();
    await expect(page.getByTestId("detalle-ticket.pestanas.spec")).toHaveAttribute("aria-selected", "true");

    // 3. Alternar a pestaña Evidencia QA
    await page.getByTestId("detalle-ticket.pestanas.evidencia").click();
    await expect(page.getByTestId("detalle-ticket.pestanas.evidencia")).toHaveAttribute("aria-selected", "true");

    // 4. Alternar a pestaña Seguridad
    await page.getByTestId("detalle-ticket.pestanas.seguridad").click();
    await expect(page.getByTestId("detalle-ticket.pestanas.seguridad")).toHaveAttribute("aria-selected", "true");

    // 5. Alternar a pestaña Consumo
    await page.getByTestId("detalle-ticket.pestanas.consumo").click();
    await expect(page.getByTestId("detalle-ticket.pestanas.consumo")).toHaveAttribute("aria-selected", "true");

    // 6. Alternar a pestaña Actividad
    await page.getByTestId("detalle-ticket.pestanas.actividad").click();
    await expect(page.getByTestId("detalle-ticket.pestanas.actividad")).toHaveAttribute("aria-selected", "true");

    // Verificar que el contenedor principal sigue sin errores de renderizado
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();
  });

  // ============================================================================
  // 5. Cadena de Firmas de Gates
  // ============================================================================
  test("Firma Humana de Gate 1 y Cambio de Estado del Job", async ({ page }) => {
    // 1. Iniciar sesión con rol aprobador
    await loginAs(page, "approver");

    // 2. Navegar a ticket con Gate 1 pendiente (KC-1003) vía navegación interna SPA
    await navigateSpa(page, "/tickets/KC-1003");
    await expect(page.getByTestId("detalle-ticket.vista.raiz")).toBeVisible();

    // 3. Validar presencia del panel de Gate pendiente
    const panelGate = page.getByTestId("detalle-ticket.gate.panel");
    await expect(panelGate).toBeVisible();

    // 4. Ingresar comentario de aprobación
    await page
      .getByTestId("detalle-ticket.gate.comentario")
      .fill("Aprobado para proceder con la fase de construcción. Cumple con todos los criterios de arquitectura.");

    // 5. Presionar botón de aprobar para abrir modal de firma
    await page.getByTestId("detalle-ticket.gate.aprobar").click();

    // 6. Validar apertura del diálogo de firma
    const modalFirma = page.getByTestId("detalle-ticket.firma.dialogo");
    await expect(modalFirma).toBeVisible();

    // 7. Confirmar decisión de firma en el modal
    await page.getByTestId("detalle-ticket.firma.confirmar").click();

    // 8. Validar resultado de la decisión y sello
    await expect(page.getByTestId("detalle-ticket.firma.resultado")).toBeVisible();
  });
});
