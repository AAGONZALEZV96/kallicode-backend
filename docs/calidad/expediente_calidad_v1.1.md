# Expediente de Calidad y Matriz de Trazabilidad — v1.1

- **Veredicto Release Gate GA:** `APROBADO (M6 GA READY)`
- **Versión Release:** `v1.1`
- **Commit SHA:** `local-dev`
- **Generado (UTC):** `2026-11-19T23:32:08+00:00`
- **Hash Raíz WORM (SHA-256):** `f88ffdf8f780f2e936d2ec642c1ecf62ade5fe7592340858890c7f7c72c0eff8`

## Resumen de Compuertas de Calidad

| Compuerta | Medición | Umbral Exigido | Estado |
| :--- | :--- | :--- | :--- |
| Cobertura Global | 88.66% | >= 80% | PASS |
| Cobertura Seguridad y Auditoría | 98.11% | >= 95% | PASS |
| Criterios BDD Aprobados | 8/8 | 100% (> 0) | PASS |
| Cuarentena Sin Ticket Desflake | 0 | 0 | PASS |
| Violaciones `core.validar_integridad()` | 0 | 0 | PASS |

## Matriz de Trazabilidad Criterio a Test

| Criterio | Descripción | Prueba Asociada | Tipo | Estado | Evidencia |
| :--- | :--- | :--- | :--- | :--- | :--- |
| UAT-01 | Owner / Admin: Creación de tickets y paso a Triage | `packages/testing/e2e/tests/portal_critical_flows.spec.ts` | e2e | PASS | `Playwright Trace + Reporte JSON` |
| UAT-02 | Owner / Admin: Onboarding de repositorio y encendido de fábrica | `packages/testing/tests/contract/test_internal_api.py` | integration | PASS | `Reporte Contrato JUnit + Digest WORM` |
| UAT-03 | Owner / Admin: Monitoreo de producción y cuotas de consumo | `packages/testing/tests/commercial/test_billing_mock.py` | integration | PASS | `Reporte JSON Billing + k6 Ingest Report` |
| UAT-04 | Architect: Revisión técnica y firma de Gate 1 | `packages/testing/e2e/tests/portal_critical_flows.spec.ts` | e2e | PASS | `Captura Visual + Evento Auditoría WORM` |
| UAT-05 | Architect: Preflight de despliegue y firma de Gate 3 | `packages/testing/tests/unit/test_dossier_builder.py` | integration | PASS | `Expediente JSON `dossier_v1.1.json` + SHA-256` |
| UAT-06 | Approver: Inspección QA y firma de Gate 2 | `packages/testing/e2e/tests/visual_regression.spec.ts` | e2e | PASS | `Capturas PNG/Video + `visual-diff-report.json`` |
| UAT-07 | Approver: Devolución controlada (`cambios_pedidos`) | `packages/testing/tests/contract/test_openapi_contract.py` | integration | PASS | `Reporte OpenAPI Schema + JUnit XML` |
| UAT-08 | Viewer: Consulta solo-lectura con rechazo HTTP 403 | `packages/testing/tests/seguridad/test_rbac_matrix.py` | integration | PASS | `Reporte Matriz RBAC + Digest WORM` |

## Manifiesto Criptográfico WORM (SHA-256)

| Artefacto | Ruta Relativa | Tamaño (bytes) | SHA-256 |
| :--- | :--- | ---: | :--- |
| `estabilizacion-codemapping.md` | `estabilizacion-codemapping.md` | 9726 | `c52b779c3156f914676083a209023dfa8192e0c31597ada0976ca8039e489249` |
| `estrategia_testing_local.md` | `estrategia_testing_local.md` | 2698 | `28889fb4bd7b68adfbfa0b230af9941a574d406f1ff4ccf58d6b2dcc7d3cc7a0` |
| `informe-calidad-cm.md` | `informe-calidad-cm.md` | 14723 | `e30d6805c22e2a663e843f4aa66c9b5c601d929f6b28681f1371f8191edf7d0a` |
| `informe_seguridad_final_v1.1.md` | `informe_seguridad_final_v1.1.md` | 4158 | `7fb3650f37c0f4408fe5840b36aed9a8790cfb78e35e5d4b4dfb683cd26764f3` |
| `linea_base_calidad_cm.json` | `linea_base_calidad_cm.json` | 5049 | `68587bbe9dc65fa7a7d939e41be772b9ad0cf3b768dc70a0a9d6867912c5db07` |
| `mutacion_codemapping.md` | `mutacion_codemapping.md` | 4459 | `ac011f3a293f9ae23c8fdde70d70294a86520c522f45e0f5c8c4b1f1c758b6e5` |
| `uat_signoff_matrix.md` | `uat_signoff_matrix.md` | 4120 | `0090f06851f0a958d14c16cca444ae4796f237574a8b81d2412e9a73f451130d` |
