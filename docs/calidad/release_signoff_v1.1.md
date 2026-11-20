# Certificado Formal de Sign-Off e Inmutabilidad — v1.1

- **Veredicto GA:** `APROBADO Y SELLADO (M6 GA READY)`
- **Versión Release:** `v1.1`
- **Commit SHA:** `4ed274d0`
- **Emitido (UTC):** `2026-11-20T00:21:18.473449+00:00`
- **Archivos Sellados:** `490`
- **Root Hash SHA-256:** `019f50b86a675e4d6c2d629b97a02660cd79933d9207d69c46b0b5b94d9b2c88`

## Protocolo Formal de 4 Firmas Obligatorias

| Rol | Firmante | Decisión | Timestamp (UTC) | Declaración | Checksum Firma |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `PO` | Product Owner (KalliCode) | APPROVED | `2026-11-20T00:21:17.851511+00:00` | Aprobación funcional, de alcance de producto y cumplimiento 100% UAT. | `770a2ee68a94a4a9...` |
| `PLT1` | Tech Lead Plataforma (PLT1) | APPROVED | `2026-11-20T00:21:17.851511+00:00` | Aprobación de arquitectura, escalabilidad, carga k6 y recuperación ante caos. | `8af57f6b8c86b097...` |
| `CAL1 / QA1` | Tech Lead QA & SRE (CAL1 / QA1) | APPROVED | `2026-11-20T00:21:17.851511+00:00` | Aprobación de cobertura global >= 80%, seguridad >= 95% y 0 bloqueadores. | `3036417033c1ad8d...` |
| `PLT4` | Ingeniero de Seguridad (PLT4) | APPROVED | `2026-11-20T00:21:17.851511+00:00` | Aprobación de postura OWASP Top 10, Gitleaks limpio y 0 CVEs Critical/High. | `c60aae5c72663d0c...` |

## Resumen de Alcance Criptográfico

| Parámetro / Directorio Sellado | Valor |
| :--- | :--- |
| Objetivos escaneados | `apps/api, packages/core, packages/testing, db, docs/calidad/expediente_calidad_v1.1.md, docs/calidad/uat_signoff_matrix.md, docs/calidad/informe_seguridad_final_v1.1.md` |
| Exclusiones aplicadas | `__pycache__, *.pyc, *.pyo, .pytest_cache, .mypy_cache, .ruff_cache, htmlcov, coverage.xml, .coverage, .coverage.*, .DS_Store, artifacts, test-results, playwright-report, .auth, release_signoff_v1.1.json, release_signoff_v1.1.md` |
| Total de archivos en manifiesto | `490` |
| Integridad `is_tamper_free` | `True` |
| Firmas completas `is_fully_signed` | `True` |
