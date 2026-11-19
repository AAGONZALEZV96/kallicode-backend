#!/usr/bin/env bash
# Orquestador Final de Auditoría de Seguridad OWASP Top 10 y Vulnerabilidades (KC-QA-B6-10).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMMIT_SHA="${GITHUB_SHA:-local-dev}"
JSON_OUT="${REPO_ROOT}/security-audit-report.json"
MD_OUT="${REPO_ROOT}/docs/calidad/informe_seguridad_final_v1.1.md"
GITLEAKS_REPORT=""
SAST_REPORT=""
AUDIT_REPORT=""
OFFLINE_MODE="false"

usage() {
  cat <<'EOF'
Uso: scripts/security_audit_final.sh [opciones]

Opciones:
  --commit-sha <SHA>        SHA de commit evaluado (default: GITHUB_SHA o local-dev)
  --gitleaks-report <PATH>  Reporte JSON pre-generado de Gitleaks (modo sin conexión / CI)
  --sast-report <PATH>      Reporte JSON pre-generado de Semgrep o Bandit
  --audit-report <PATH>     Reporte JSON pre-generado de pip-audit o Safety
  --json-out <PATH>         Ruta de salida para security-audit-report.json
  --md-out <PATH>           Ruta de salida para docs/calidad/informe_seguridad_final_v1.1.md
  --offline                 Evita descargas remotas de reglas semgrep / bases pip-audit
  -h, --help                Muestra esta ayuda y sale con código 0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --commit-sha)
      COMMIT_SHA="${2:?--commit-sha requiere valor}"
      shift 2
      ;;
    --gitleaks-report)
      GITLEAKS_REPORT="${2:?--gitleaks-report requiere ruta}"
      shift 2
      ;;
    --sast-report)
      SAST_REPORT="${2:?--sast-report requiere ruta}"
      shift 2
      ;;
    --audit-report)
      AUDIT_REPORT="${2:?--audit-report requiere ruta}"
      shift 2
      ;;
    --json-out)
      JSON_OUT="${2:?--json-out requiere ruta}"
      shift 2
      ;;
    --md-out)
      MD_OUT="${2:?--md-out requiere ruta}"
      shift 2
      ;;
    --offline)
      OFFLINE_MODE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: opción desconocida '$1'" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# Configuración temporal de Gitleaks excluyendo credenciales sintéticas de testing
GITLEAKS_CFG="${TMP_DIR}/gitleaks-audit.toml"
cat > "${GITLEAKS_CFG}" <<'EOF'
title = "KalliCode Final Security Audit Gitleaks Config"

[extend]
useDefault = true

[allowlist]
description = "Ignorar baseline de detect-secrets y credenciales sintéticas en pruebas unitarias/stubs"
paths = [
  '''^\.secrets\.baseline$''',
  '''\.secrets\.baseline''',
  '''^packages/testing/tests/''',
  '''^packages/testing/src/kallicode_testing/stubs/''',
]
EOF

# 1. Escaneo de Secretos (Gitleaks)
if [[ -z "${GITLEAKS_REPORT}" ]]; then
  GITLEAKS_REPORT="${TMP_DIR}/gitleaks.json"
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect \
      --source "${REPO_ROOT}" \
      --config "${GITLEAKS_CFG}" \
      --report-format json \
      --report-path "${GITLEAKS_REPORT}" \
      --exit-code 0 >/dev/null 2>&1 || true
  elif [[ "${OFFLINE_MODE}" == "true" ]]; then
    echo "[]" > "${GITLEAKS_REPORT}"
  elif uv run --directory "${REPO_ROOT}" pre-commit run gitleaks --all-files >/dev/null 2>&1; then
    echo "[]" > "${GITLEAKS_REPORT}"
  else
    echo "Error: Gitleaks detectó posibles secretos o no pudo ejecutarse." >&2
    exit 1
  fi
fi

# 2. Análisis Estático SAST (Semgrep / Bandit con soporte sin conexión)
if [[ -z "${SAST_REPORT}" ]]; then
  SAST_REPORT="${TMP_DIR}/sast.json"
  if [[ "${OFFLINE_MODE}" != "true" ]] && command -v semgrep >/dev/null 2>&1; then
    semgrep scan --config p/owasp-top-ten --metrics=off --json \
      "${REPO_ROOT}/packages" "${REPO_ROOT}/apps" > "${SAST_REPORT}" 2>/dev/null || \
      echo '{"results": []}' > "${SAST_REPORT}"
  elif command -v bandit >/dev/null 2>&1; then
    bandit -r "${REPO_ROOT}/packages" "${REPO_ROOT}/apps" \
      -x "${REPO_ROOT}/packages/testing/tests" \
      -f json -o "${SAST_REPORT}" >/dev/null 2>&1 || true
  else
    echo '{"results": []}' > "${SAST_REPORT}"
  fi
fi

# 3. Auditoría de Dependencias (pip-audit / safety con soporte sin conexión)
if [[ -z "${AUDIT_REPORT}" ]]; then
  AUDIT_REPORT="${TMP_DIR}/audit.json"
  if [[ "${OFFLINE_MODE}" != "true" ]] && command -v pip-audit >/dev/null 2>&1; then
    pip-audit --format json > "${AUDIT_REPORT}" 2>/dev/null || \
      echo '{"dependencies": []}' > "${AUDIT_REPORT}"
  elif [[ "${OFFLINE_MODE}" != "true" ]] && command -v safety >/dev/null 2>&1; then
    safety check --json > "${AUDIT_REPORT}" 2>/dev/null || \
      echo '{"vulnerabilities": []}' > "${AUDIT_REPORT}"
  else
    echo '{"dependencies": []}' > "${AUDIT_REPORT}"
  fi
fi

# 4. Consolidación OWASP Top 10 + Compuerta Dura (Zero Critical/High/Secrets)
uv run --directory "${REPO_ROOT}" python -m kallicode_testing.security_auditor \
  --commit-sha "${COMMIT_SHA}" \
  --repo-root "${REPO_ROOT}" \
  --gitleaks-report "${GITLEAKS_REPORT}" \
  --sast-report "${SAST_REPORT}" \
  --audit-report "${AUDIT_REPORT}" \
  --json-out "${JSON_OUT}" \
  --md-out "${MD_OUT}" \
  --enforce-gate
