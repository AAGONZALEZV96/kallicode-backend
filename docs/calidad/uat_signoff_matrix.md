# Matriz Oficial de Aceptación de Usuario (UAT) — KalliCode v1.1

- **Documento:** `docs/calidad/uat_signoff_matrix.md`
- **Ticket:** KC-QA-B6-08 (Épica EQA8, Bloque 6, Hito M6 GA)
- **Política de Aprobación:** 100% de escenarios `PASSED` con referencia de prueba automatizada y evidencia trazable.

## Resumen por Rol de Negocio

| Rol | Total Escenarios | Aprobados | Cumplimiento |
| :--- | ---: | ---: | ---: |
| Owner / Admin | 3 | 3 | 100.00% |
| Architect | 2 | 2 | 100.00% |
| Approver | 2 | 2 | 100.00% |
| Viewer | 1 | 1 | 100.00% |

## Matriz Exhaustiva de Escenarios UAT

| Scenario ID | Rol | Título | Descripción | Prueba Automatizada | Tipo Evidencia | Estado | Notas |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `UAT-01` | Owner / Admin | Creación de tickets y paso a Triage | Creación de tickets (bug/mejora) en el portal y transición controlada hacia estado Triage. | `packages/testing/e2e/tests/portal_critical_flows.spec.ts` | Playwright Trace + Reporte JSON | PASSED | Flujo crítico de onboarding y triage verificado sin errores 5xx. |
| `UAT-02` | Owner / Admin | Onboarding de repositorio y encendido de fábrica | Conexión de repositorio sintético y verificación de precondiciones operacionales antes de iniciar ingesta. | `packages/testing/tests/contract/test_internal_api.py` | Reporte Contrato JUnit + Digest WORM | PASSED | Validación de precondiciones y aislamiento multi-tenant en ingesta inicial. |
| `UAT-03` | Owner / Admin | Monitoreo de producción y cuotas de consumo | Supervisión de métricas operacionales, consumo LOC/storage y enforcement de límites comerciales. | `packages/testing/tests/commercial/test_billing_mock.py` | Reporte JSON Billing + k6 Ingest Report | PASSED | Cuotas LOC/storage y respuestas HTTP 429 verificadas bajo contrato. |
| `UAT-04` | Architect | Revisión técnica y firma de Gate 1 | Inspección de especificaciones técnicas, alternativas de diseño arquitectónico y aprobación formal de Gate 1. | `packages/testing/e2e/tests/portal_critical_flows.spec.ts` | Captura Visual + Evento Auditoría WORM | PASSED | Transición explícita de Gate 1 sin aprobaciones implícitas. |
| `UAT-05` | Architect | Preflight de despliegue y firma de Gate 3 | Verificación de preflight operacional, expediente de calidad GA y firma obligatoria de Gate 3. | `packages/testing/tests/unit/test_dossier_builder.py` | Expediente JSON `dossier_v1.1.json` + SHA-256 | PASSED | Compuerta dura de release validando cobertura >= 80% y seguridad >= 95%. |
| `UAT-06` | Approver | Inspección QA y firma de Gate 2 | Revisión de matriz de resultados QA, evidencias visuales (video/captura) y aprobación explícita de Gate 2. | `packages/testing/e2e/tests/visual_regression.spec.ts` | Capturas PNG/Video + `visual-diff-report.json` | PASSED | Evidencias visuales selladas verificadas antes de liberar Gate 2. |
| `UAT-07` | Approver | Devolución controlada (`cambios_pedidos`) | Retorno controlado de entregas con observaciones de negocio y reentrada trazable de comentarios al pipeline. | `packages/testing/tests/contract/test_openapi_contract.py` | Reporte OpenAPI Schema + JUnit XML | PASSED | Payload de rechazo y reentrada de comentarios auditados por esquema. |
| `UAT-08` | Viewer | Consulta solo-lectura con rechazo HTTP 403 | Consulta del tablero Kanban y expediente de auditoría garantizando rechazo HTTP 403 ante mutación o firma. | `packages/testing/tests/seguridad/test_rbac_matrix.py` | Reporte Matriz RBAC + Digest WORM | PASSED | Control RBAC estricto para rol Viewer sin escalación de privilegios. |

## Manifiesto de Firmas de Aprobación (Hito M6 GA)

| Autoridad Firmante | Rol en Gobernanza | Estado de Firma | Evidencia / Criterio |
| :--- | :--- | :--- | :--- |
| Product Owner | Validación Funcional de Negocio | FIRMADO | 8/8 escenarios UAT aprobados (100%) |
| Architecture Board | Aprobación Técnica y Gates 1-3 | FIRMADO | Trazabilidad completa y contratos OpenAPI validados |
| Tech Lead QA (Equipo 5) | Certificación Cero Falsos Verdes | FIRMADO | Validado vía `scripts/verify_uat.py` y `UatValidator` |
