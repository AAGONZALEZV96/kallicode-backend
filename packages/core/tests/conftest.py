from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def mock_embeddings_global(monkeypatch):
    """
    Mock global (KC-PLT-02-5): Intercepta automáticamente cualquier intento
    de generar embeddings reales durante la suite de pruebas.
    Evita timeouts en CI/CD y costos de API.
    Retorna un vector dummy de 1024 dimensiones (formato bge-m3).
    """
    vector_dummy = [0.015] * 1024

    # Creamos un reemplazo asíncrono que devuelva el vector instantáneamente
    mock_embeber = AsyncMock(return_value=vector_dummy)

    # Inyectamos el mock directamente en el módulo que hace la llamada HTTP.
    # Nota: Si el equipo mueve la función a 'servicios.py', actualiza esta ruta.
    try:
        monkeypatch.setattr("kallicode_api.routers.tickets._embeber", mock_embeber)
    except AttributeError:
        # Falla silenciosa permitida si el test actual no importa los routers (ej. pruebas unitarias puras)
        pass

    return mock_embeber
