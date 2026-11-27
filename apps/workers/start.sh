#!/usr/bin/env bash
# Render Web Service: la API pública despierta el contenedor y el worker.
set -euo pipefail

PORT="${PORT:-10000}"
puerto_interno=8001
if [[ "$PORT" == "$puerto_interno" ]]; then
    echo "El puerto público de Render debe ser distinto del puerto interno 8001." >&2
    exit 1
fi

export KC_INTERNAL_API_BASE_URL="http://127.0.0.1:${puerto_interno}/internal/v1"
export KC_WORKERS_CONTAINER=true

pids=()
detener() {
    if (( ${#pids[@]} )); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap detener EXIT
trap 'exit 0' INT TERM

# Las operaciones internas y /metrics quedan accesibles solo dentro del contenedor.
uvicorn kallicode_api.main_interna:app --host 127.0.0.1 --port "$puerto_interno" --no-proxy-headers &
pids+=("$!")
python - "$puerto_interno" <<'PY'
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

url = f"http://127.0.0.1:{sys.argv[1]}/health"
limite = time.monotonic() + 30
while time.monotonic() < limite:
    try:
        with urlopen(url, timeout=1) as respuesta:
            if respuesta.status == 200:
                sys.exit(0)
    except (URLError, TimeoutError):
        time.sleep(0.25)
sys.exit("La API interna de Kallicode no arrancó dentro del plazo.")
PY

python -m kallicode_workers.main &
pids+=("$!")
uvicorn kallicode_api.main:app --host 0.0.0.0 --port "$PORT" &
pids+=("$!")

# Render debe reiniciar el conjunto si se detiene cualquiera de los tres procesos.
wait -n "${pids[@]}" || true
exit 1
