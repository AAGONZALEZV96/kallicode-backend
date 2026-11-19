# Certificado Formal de Sign-Off e Inmutabilidad — v1.1

- **Veredicto GA:** `APROBADO Y SELLADO (M6 GA READY)`
- **Versión Release:** `v1.1`
- **Commit SHA:** `e9ab167e`
- **Emitido (UTC):** `2026-11-19T21:03:43.382456+00:00`
- **Archivos Sellados:** `509`
- **Root Hash SHA-256:** `52646824bb62e09bbe32182e1f04523697083a5752b77758b3fce415736722b0`

## Protocolo Formal de 4 Firmas Obligatorias

| Rol | Firmante | Decisión | Timestamp (UTC) | Declaración | Checksum Firma |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `PO` | Product Owner (KalliCode) | APPROVED | `2026-11-19T21:03:41.707738+00:00` | Aprobación funcional, de alcance de producto y cumplimiento 100% UAT. | `0225f5540409b720...` |
| `PLT1` | Tech Lead Plataforma (PLT1) | APPROVED | `2026-11-19T21:03:41.707738+00:00` | Aprobación de arquitectura, escalabilidad, carga k6 y recuperación ante caos. | `16b409ef2dafa523...` |
| `CAL1 / QA1` | Tech Lead QA & SRE (CAL1 / QA1) | APPROVED | `2026-11-19T21:03:41.707738+00:00` | Aprobación de cobertura global >= 80%, seguridad >= 95% y 0 bloqueadores. | `ff8ebb8bdb09db76...` |
| `PLT4` | Ingeniero de Seguridad (PLT4) | APPROVED | `2026-11-19T21:03:41.707738+00:00` | Aprobación de postura OWASP Top 10, Gitleaks limpio y 0 CVEs Critical/High. | `078ac4b28c7046e2...` |

## Resumen de Alcance Criptográfico

| Parámetro / Directorio Sellado | Valor |
| :--- | :--- |
| Objetivos escaneados | `apps/api, packages/core, packages/testing, db, docs/calidad/expediente_calidad_v1.1.md, docs/calidad/uat_signoff_matrix.md, docs/calidad/informe_seguridad_final_v1.1.md` |
| Exclusiones aplicadas | `__pycache__, *.pyc, *.pyo, .pytest_cache, .mypy_cache, .ruff_cache, htmlcov, coverage.xml, .coverage, .coverage.*, .DS_Store, release_signoff_v1.1.json, release_signoff_v1.1.md` |
| Total de archivos en manifiesto | `509` |
| Integridad `is_tamper_free` | `True` |
| Firmas completas `is_fully_signed` | `True` |
