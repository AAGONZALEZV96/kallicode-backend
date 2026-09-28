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
    mock_embeber = AsyncMock(return_value=vector_dummy)

    # Solo hacemos el mock si el módulo existe
    try:
        import kallicode_api.routers.tickets

        if hasattr(kallicode_api.routers.tickets, "_embeber"):
            monkeypatch.setattr("kallicode_api.routers.tickets._embeber", mock_embeber)
    except (ImportError, AttributeError):
        # El módulo no existe aún, saltamos el mock silenciosamente
        pass

    return mock_embeber
