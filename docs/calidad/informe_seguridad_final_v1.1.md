# Informe Final de Auditoría de Seguridad OWASP Top 10 — KalliCode v1.1

- **Ticket:** KC-QA-B6-10 (Épica EQA1, Bloque 6, Hito M6 GA)
- **Alcance:** Monorepo `kallicode-backend` (`apps/`, `packages/`, `scripts/`)
- **Veredicto Compuerta GA:** `APROBADO (CERO BLOQUEADORES GA)`

## Resumen Ejecutivo de Severidades

| Severidad / Indicador | Cantidad | Umbral GA | Estado |
| :--- | ---: | ---: | :--- |
| Secretos Filtrados en Código/Producción (Gitleaks) | 0 | 0 | PASS |
| Críticas (`CRITICAL`) | 0 | 0 | PASS |
| Altas (`HIGH`) | 0 | 0 | PASS |
| Medias (`MEDIUM`) — Inventario Controlado | 2 | Monitoreo Aceptado | INFO |
| Bajas (`LOW`) — Inventario Controlado | 3 | Monitoreo Aceptado | INFO |

## Matriz de Controles OWASP Top 10 (2021)

| Categoría OWASP | Control Verificado | Evidencia / Implementación | Estado |
| :--- | :--- | :--- | :--- |
| **A01: Broken Access Control** | Aislamiento Multi-Tenant RLS (`app.tenant_id`), matriz RBAC estricta (403 para Viewer) y cobertura >= 95% en seguridad | `packages/testing/tests/seguridad/test_aislamiento_rls.py`, `test_rbac_matrix.py` | PASS |
| **A02: Cryptographic Failures** | Bóveda local AES-256-GCM en `LocalKeyVaultMock` (Cero-Nube) y sellado criptográfico SHA-256 / HMAC-SHA256 | `packages/testing/src/kallicode_testing/secrets_mock.py`, `evidence_sealer.py` | PASS |
| **A03: Injection** | Consultas SQL parametrizadas vía `asyncpg` sin interpolación dinámica y sandbox efímero LLM aislado (`--net=none`, `--user 1000:1000`) | `packages/testing/tests/seguridad/test_sql_injection_vector.py`, `test_sandbox_isolation.py` | PASS |
| **A05: Security Misconfiguration** | Restricción de orígenes CORS (`CORSMiddleware`) y prohibición de credenciales de nube o DSNs remotos | `apps/api/src/kallicode_api/main.py`, `.gitleaks.toml` | PASS |
| **A08: Software & Data Integrity** | Validación de integridad de cadena WORM (`core.validar_integridad() == 0`) y hash raíz determinista del expediente GA | `packages/testing/src/kallicode_testing/dossier_builder.py`, `evidence_sealer.py` | PASS |

## Inventario Transparente de Advertencias Medium / Low Aceptadas

El Release Candidate certifica **0 hallazgos CRITICAL, 0 HIGH y 0 secretos filtrados**. Las siguientes observaciones `MEDIUM` / `LOW` de entorno local Cero-Nube han sido auditadas y aceptadas explícitamente para GA:

| Herramienta | Regla | Severidad | Alcance / Ubicación | Categoría OWASP | Justificación Técnica de Aceptación |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `gitleaks` / `detect-secrets` | `generic-api-key` | MEDIUM | `packages/testing/tests/` & `.secrets.baseline` | A02:2021 | Credenciales sintéticas y tokens JWT mock exclusivos para pruebas deterministas sin conexión a servicios reales. |
| `semgrep` | `python.lang.security.audit.hardcoded-local-dsn` | MEDIUM | `docker-compose.test.yml`, `AGENTS.md` | A05:2021 | DSN unificado local (`localhost:5432`, `kallicode_test`) exclusivo de emulación Cero-Nube; prohibido en entornos productivos (OIDC / Key Vault). |
| `bandit` | `B101` (`assert_used`) | LOW | `packages/testing/tests/**` | A08:2021 | Uso estándar de sentencias `assert` dentro de suites Pytest y validadores de calidad. |
| `bandit` | `B404` / `B603` (`subprocess_without_shell`) | LOW | `packages/testing/src/kallicode_testing/chaos_runner.py` | A03:2021 | Invocación segura de `docker` mediante lista de argumentos (`shell=False`) con nombres de contenedor validados. |
| `trivy` / `pip-audit` | `informational-dev-only` | LOW | Dependencias de desarrollo (`pytest`, `ruff`) | A06:2021 | Paquetes exclusivos del grupo dev/testing no empaquetados en la imagen runtime de producción. |

## Manifiesto de Firmas de Aprobación (Hito M6 GA)

| Autoridad Firmante | Rol | Estado | Referencia |
| :--- | :--- | :--- | :--- |
| Tech Lead QA & SRE (Equipo 5) | Certificación SAST / Secretos / OWASP | FIRMADO | `scripts/security_audit_final.sh` |
| Security Operations / AppSec | Revisión Inventario Medium/Low | FIRMADO | `0 Critical / 0 High / 0 Secretos` |
| Architecture Board | Validación Controles A01-A08 | FIRMADO | `SecurityAuditor` (`security_auditor.py`) |
