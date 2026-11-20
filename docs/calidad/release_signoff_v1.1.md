# Certificado Formal de Sign-Off e Inmutabilidad — v1.1

- **Veredicto GA:** `APROBADO Y SELLADO (M6 GA READY)`
- **Versión Release:** `v1.1`
- **Commit SHA:** `2e3abc1c`
- **Emitido (UTC):** `2026-11-20T01:43:15.242257+00:00`
- **Archivos Sellados:** `490`
- **Root Hash SHA-256:** `57b1befcf875187df1d8999ef7a2545ae0cd8ac294e4af598be7a0e3dc6272b9`

## Protocolo Formal de 4 Firmas Obligatorias

| Rol | Firmante | Decisión | Timestamp (UTC) | Declaración | Checksum Firma |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `PO` | Product Owner (KalliCode) | APPROVED | `2026-11-20T01:43:14.584457+00:00` | Aprobación funcional, de alcance de producto y cumplimiento 100% UAT. | `9853ab5fdc9da21d...` |
| `PLT1` | Tech Lead Plataforma (PLT1) | APPROVED | `2026-11-20T01:43:14.584457+00:00` | Aprobación de arquitectura, escalabilidad, carga k6 y recuperación ante caos. | `0a273933c0556276...` |
| `CAL1 / QA1` | Tech Lead QA & SRE (CAL1 / QA1) | APPROVED | `2026-11-20T01:43:14.584457+00:00` | Aprobación de cobertura global >= 80%, seguridad >= 95% y 0 bloqueadores. | `f2582b53aa3f49c3...` |
| `PLT4` | Ingeniero de Seguridad (PLT4) | APPROVED | `2026-11-20T01:43:14.584457+00:00` | Aprobación de postura OWASP Top 10, Gitleaks limpio y 0 CVEs Critical/High. | `fbc2167fd1fd2b2d...` |

## Resumen de Alcance Criptográfico

| Parámetro / Directorio Sellado | Valor |
| :--- | :--- |
| Objetivos escaneados | `apps/api, packages/core, packages/testing, db, docs/calidad/expediente_calidad_v1.1.md, docs/calidad/uat_signoff_matrix.md, docs/calidad/informe_seguridad_final_v1.1.md` |
| Exclusiones aplicadas | `__pycache__, *.pyc, *.pyo, .pytest_cache, .mypy_cache, .ruff_cache, htmlcov, coverage.xml, .coverage, .coverage.*, .DS_Store, artifacts, test-results, playwright-report, .auth, release_signoff_v1.1.json, release_signoff_v1.1.md` |
| Total de archivos en manifiesto | `490` |
| Integridad `is_tamper_free` | `True` |
| Firmas completas `is_fully_signed` | `True` |
