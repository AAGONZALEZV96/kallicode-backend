#!/usr/bin/env bash

set -euo pipefail

PORT="${PORT:-10000}"

export KC_INTERNAL_API_BASE_URL="${KC_INTERNAL_API_BASE_URL:-http://127.0.0.1:${PORT}/internal/v1}"

# Esperar 3s a que uvicorn abra /internal/v1 antes de arrancar el orquestador
(
    sleep 3 && python -m kallicode_workers.main
) &

exec uvicorn kallicode_api.main_interna:app \
    --host 0.0.0.0 \
    --port "$PORT"