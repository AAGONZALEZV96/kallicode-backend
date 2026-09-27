import json
import sys
from pathlib import Path

# Inyectar ruta para que pytest encuentre tu API si es necesario
ruta_api = Path("apps/api/src").resolve()
if str(ruta_api) not in sys.path:
    sys.path.insert(0, str(ruta_api))

from kallicode_api.main import app


def test_contrato_internal_v1_intacto():
    """
    Verifica que el contrato OpenAPI de /internal/v1 no haya sufrido
    cambios no documentados que puedan romper la integracion.
    """
    # 1. Leer el archivo congelado (nuestra fuente de verdad)
    ruta_contrato = Path("docs/openapi_internal_v1.json").resolve()
    assert ruta_contrato.exists(), "El archivo del contrato congelado no existe en docs/"

    with open(ruta_contrato, encoding="utf-8") as f:
        contrato_congelado = json.load(f)

    # 2. Obtener el esquema de la API en vivo
    esquema_actual = app.openapi()

    # Filtrar solo /internal/v1
    paths_internos = {}
    for ruta, metodos in esquema_actual.get("paths", {}).items():
        if ruta.startswith("/internal/v1"):
            paths_internos[ruta] = metodos

    esquema_actual["paths"] = paths_internos

    # 3. Comparar ambos diccionarios
    # Si son distintos, el test falla y muestra este mensaje de error
    mensaje_error = (
        "🚨 ¡ALERTA DE CONTRATO ROTO!\n"
        "El esquema de la API interna cambió respecto al archivo congelado.\n"
        "Si este cambio es intencional, debes actualizar el contrato ejecutando:\n"
        "👉 uv run python docs/congelar_contrato.py"
    )

    assert esquema_actual == contrato_congelado, mensaje_error
