import json
import sys
from pathlib import Path

# Le decimos a Python dónde encontrar la API
ruta_api = Path("apps/api/src").resolve()
sys.path.insert(0, str(ruta_api))

# Importamos la instancia FastAPI
from kallicode_api.main import app


def congelar():
    esquema = app.openapi()

    # Conservar únicamente las rutas de /internal/v1
    paths_internos = {}

    for ruta, metodos in esquema.get("paths", {}).items():
        if ruta.startswith("/internal/v1"):
            paths_internos[ruta] = metodos

    esquema["paths"] = paths_internos

    # Guardar contrato
    ruta_destino = Path("docs/openapi_internal_v1.json")
    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    with ruta_destino.open("w", encoding="utf-8") as f:
        json.dump(
            esquema,
            f,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    print(f"Contrato congelado exitosamente en: {ruta_destino}")


if __name__ == "__main__":
    congelar()