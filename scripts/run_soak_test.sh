#!/usr/bin/env bash
# Wrapper operacional de Soak Testing y Validación de Memoria (KC-QA-B6-09).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BASE_URL="${BASE_URL:-http://localhost:8000}"
SOAK_FAST_MODE="${SOAK_FAST_MODE:-true}"
SOAK_DURATION="${SOAK_DURATION:-}"
SOAK_VUS="${SOAK_VUS:-}"
SUMMARY_FILE="${SUMMARY_FILE:-artifacts/soak-summary.json}"
REPORT_FILE="${REPORT_FILE:-artifacts/soak-report.json}"
SKIP_VERIFY="false"

usage() {
  cat <<'EOF'
Uso: scripts/run_soak_test.sh [opciones]

Opciones:
  --fast                 Fuerza modo rápido sintético (SOAK_FAST_MODE=true)
  --full                 Fuerza corrida completa nocturna de 4 horas
  --vus <N>              Número de VUs concurrentes (override)
  --duration <DUR>       Duración k6 (ej. 30s, 4h)
  --base-url <URL>       URL base del backend local (default: http://localhost:8000)
  --summary-file <PATH>  Ruta de salida soak-summary.json
  --report-file <PATH>   Ruta de salida soak-report.json
  --skip-verify          Omite el análisis final con SoakReporter
  -h, --help             Muestra esta ayuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fast)
      SOAK_FAST_MODE="true"
      shift
      ;;
    --full)
      SOAK_FAST_MODE="false"
      shift
      ;;
    --vus)
      SOAK_VUS="${2:?--vus requiere un valor numérico}"
      shift 2
      ;;
    --duration)
      SOAK_DURATION="${2:?--duration requiere un valor}"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2:?--base-url requiere una URL}"
      shift 2
      ;;
    --summary-file)
      SUMMARY_FILE="${2:?--summary-file requiere una ruta}"
      shift 2
      ;;
    --report-file)
      REPORT_FILE="${2:?--report-file requiere una ruta}"
      shift 2
      ;;
    --skip-verify)
      SKIP_VERIFY="true"
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

mkdir -p "$(dirname "${REPO_ROOT}/${SUMMARY_FILE}")"
mkdir -p "$(dirname "${REPO_ROOT}/${REPORT_FILE}")"

K6_SCRIPT_REL="packages/testing/carga/k6_soak_test.js"
K6_EXTRA_ARGS=()
if [[ -n "${SOAK_VUS}" ]]; then
  K6_EXTRA_ARGS+=(--vus "${SOAK_VUS}")
fi
if [[ -n "${SOAK_DURATION}" ]]; then
  K6_EXTRA_ARGS+=(--duration "${SOAK_DURATION}")
fi

if command -v k6 >/dev/null 2>&1; then
  BASE_URL="${BASE_URL}" \
  SOAK_FAST_MODE="${SOAK_FAST_MODE}" \
  SOAK_DURATION="${SOAK_DURATION}" \
  SUMMARY_PATH="${REPO_ROOT}/${SUMMARY_FILE}" \
  k6 run "${K6_EXTRA_ARGS[@]}" "${REPO_ROOT}/${K6_SCRIPT_REL}"
elif command -v docker >/dev/null 2>&1; then
  docker run --rm --network host \
    -v "${REPO_ROOT}:/workspace" \
    -w /workspace \
    -e "BASE_URL=${BASE_URL}" \
    -e "SOAK_FAST_MODE=${SOAK_FAST_MODE}" \
    -e "SOAK_DURATION=${SOAK_DURATION}" \
    -e "SUMMARY_PATH=${SUMMARY_FILE}" \
    grafana/k6:0.54.0 run "${K6_EXTRA_ARGS[@]}" "${K6_SCRIPT_REL}"
else
  echo "Error: ni 'k6' ni 'docker' están disponibles en el entorno." >&2
  exit 1
fi

if [[ "${SKIP_VERIFY}" != "true" ]]; then
  uv run --directory "${REPO_ROOT}" python -m kallicode_testing.soak_reporter \
    --summary-file "${REPO_ROOT}/${SUMMARY_FILE}" \
    --output-file "${REPO_ROOT}/${REPORT_FILE}" \
    --check-soak
fi
