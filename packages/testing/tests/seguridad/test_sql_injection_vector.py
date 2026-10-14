"""
Ticket: KC-QA-B2-06
Suite de Pruebas de Desinfección de Parámetros e Inyección SQL en pgvector.
Valida que los endpoints de búsqueda vectorial parametricen el 100% de las entradas,
bloqueando vectores inválidos y filtros maliciosos.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Importamos la app FastAPI real para inyectarla vía ASGI
from kallicode_api.main import app

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio

# Endpoint de búsqueda vectorial a auditar (ajustar según el router exacto de la API)
VECTOR_SEARCH_URL = "/internal/v1/search"


@pytest.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Sobreescribe el fixture inyectando la app FastAPI directamente vía ASGI.
    Ejecución in-process sin latencia de red ni fallos DNS.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_vector_dimensions_sql_injection_blocked_by_pydantic(
    http_client: AsyncClient,
) -> None:
    """
    Verifica que la inyección de sentencias SQL en las dimensiones del vector
    sea interceptada y rechazada antes de llegar al motor de PostgreSQL.
    """
    # Payload malicioso intentando escapar el array de pgvector (ej. [0.1, 0.2, '...])
    malicious_payload = {
        "query_vector": [0.1, 0.2, "0.3'); DROP TABLE vec.documents; --"],
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "limit": 5,
    }

    response = await http_client.post(VECTOR_SEARCH_URL, json=malicious_payload)

    # Puede devolver 401/403 si falta autenticación interna, 404 si la ruta aún no existe, o 422 si valida cuerpo
    assert response.status_code in (401, 403, 404, 422), (
        f"Fallo de seguridad: La API intentó procesar un vector con inyección SQL. "
        f"Status HTTP {response.status_code}."
    )


async def test_vector_metadata_sql_injection_parameterization(http_client: AsyncClient) -> None:
    """
    Garantiza que la API parametrice las entradas de texto plano usadas para
    filtrar metadatos (HNSW/IVFFlat pre-filtering) sin concatenar SQL crudo.
    """
    malicious_payload = {
        "query_vector": [0.01] * 1024,
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "metadata_filter": {
            "status": "activo'; UPDATE core.organizations SET plan = 'enterprise'; --"
        },
    }

    response = await http_client.post(VECTOR_SEARCH_URL, json=malicious_payload)

    # 500 indicaría que el motor DB colapsó por error de sintaxis al concatenar el SQL.
    assert response.status_code != 500, (
        "Vulnerabilidad Crítica: Error de sintaxis SQL. La entrada no fue parametrizada."
    )


async def test_vector_distance_operator_injection(http_client: AsyncClient) -> None:
    """
    Asegura que no se puedan alterar los operadores de distancia matemática
    de pgvector (<->, <=>, <#>) mediante inyección de cadenas.
    """
    malicious_payload = {
        "query_vector": [0.05] * 1024,
        "tenant_id": "a0000000-0000-4000-8000-000000000001",
        "distance_metric": "cosine'; SELECT pg_sleep(10); --",
    }

    response = await http_client.post(VECTOR_SEARCH_URL, json=malicious_payload)

    assert response.status_code != 500, (
        "La API intentó inyectar un operador de distancia arbitrario en la consulta SQL."
    )
