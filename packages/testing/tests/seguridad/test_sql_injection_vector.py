"""
Ticket: KC-QA-B2-06
Suite de Pruebas de Desinfección de Parámetros e Inyección SQL en pgvector.
Valida que los endpoints de búsqueda vectorial parametricen el 100% de las entradas,
bloqueando vectores inválidos y filtros maliciosos.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import kallicode_api.deps as api_deps
import kallicode_api.routers.tickets as tickets_router
import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_core import comercial
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio

# Endpoints reales de búsqueda vectorial en KalliCode API
VECTOR_SEARCH_URL = "/api/v1/tickets/dedup-preview"
IMPACT_SEARCH_URL = "/api/v1/tickets/impact-preview"
TENANT_DEMO = "org_demo"


@pytest.fixture(autouse=True)
def aislar_dependencias_vectoriales(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Aísla dependencias de Redis y stub de embeddings para evaluar de extremo a extremo
    la validación Pydantic y la parametrización SQL real sobre pgvector en PostgreSQL 16.
    """
    monkeypatch.setattr(api_deps, "comprobar_revocacion", AsyncMock(return_value=None))
    monkeypatch.setattr(comercial, "rl_usuario", AsyncMock(return_value=None))
    monkeypatch.setattr(comercial, "rl_typing", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tickets_router,
        "_embeber",
        AsyncMock(return_value=[0.01] * 1024),
    )


def _auth_headers() -> dict[str, str]:
    token = emitir_access_token(user_id="u_dev", tenant_id=TENANT_DEMO, rol="member")
    return {"Authorization": f"Bearer {token}"}


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
    db_session: AsyncSession,
) -> None:
    """
    Verifica que la inyección de sentencias SQL en los parámetros de búsqueda vectorial
    y en las dimensiones del vector sea rechazada con 422 (nunca 404) y bloqueada por pgvector.
    """
    malicious_payload = {
        "texto": "0.3'); DROP TABLE vec.ticket_embeddings; --",
        "k": "5; DROP TABLE vec.ticket_embeddings; --",
    }

    response = await http_client.post(
        VECTOR_SEARCH_URL,
        json=malicious_payload,
        headers=_auth_headers(),
    )

    assert response.status_code != 404, "Prohibido aceptar 404 en pruebas de inyección vectorial."
    assert response.status_code == 422, (
        f"Fallo de seguridad: La API no rechazó con 422 el payload vectorial malicioso "
        f"(status {response.status_code})."
    )

    # Verificar que a nivel de motor pgvector, un literal vectorial con inyección SQL es rechazado
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("SELECT CAST(:malicious_vec AS vector)"),
                {"malicious_vec": "[0.1, 0.2, '0.3); DROP TABLE vec.ticket_embeddings; --']"},
            )

    res_table = await db_session.execute(
        text("SELECT to_regclass('vec.ticket_embeddings') IS NOT NULL")
    )
    assert res_table.scalar() is True, "La tabla vec.ticket_embeddings fue afectada."


async def test_vector_metadata_sql_injection_parameterization(
    http_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    Garantiza que la API y las consultas HNSW/pgvector parametricen las entradas
    de texto y metadatos sin concatenar SQL crudo ni producir errores 500 o 404.
    """
    malicious_payload = {
        "texto": "activo'; UPDATE core.organizations SET nombre = 'hacked'; --",
        "k": 5,
    }

    response = await http_client.post(
        VECTOR_SEARCH_URL,
        json=malicious_payload,
        headers=_auth_headers(),
    )

    assert response.status_code not in (404, 500), (
        f"Vulnerabilidad o ruta inválida en búsqueda vectorial: HTTP {response.status_code}."
    )
    assert response.status_code == 200
    assert "similares" in response.json()

    # Verificar parametrización directa de filtros de metadatos junto al operador <=> de pgvector
    await db_session.execute(text(f"SET LOCAL app.tenant_id = '{TENANT_DEMO}'"))
    vector_literal = str([0.01] * 1024)
    inyeccion_meta = "core'; UPDATE core.organizations SET nombre = 'hacked'; --"
    res_vec = await db_session.execute(
        text("""
            SELECT nombre, embedding <=> CAST(:emb AS vector) AS dist
            FROM vec.definition_embeddings
            WHERE tenant_id = :t AND modulo = :mod
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT 5
        """),
        {"t": TENANT_DEMO, "mod": inyeccion_meta, "emb": vector_literal},
    )
    assert res_vec.fetchall() == []

    res_org = await db_session.execute(
        text("SELECT nombre FROM core.organizations WHERE id = :t"),
        {"t": TENANT_DEMO},
    )
    assert res_org.scalar() != "hacked", "Inyección SQL alteró core.organizations."


async def test_vector_distance_operator_injection(
    http_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """
    Asegura que no se puedan alterar los operadores de distancia matemática
    de pgvector (<->, <=>, <#>) ni la función vec.configurar_busqueda mediante inyección.
    """
    malicious_payload = {
        "texto": "cosine'; SELECT pg_sleep(10); --",
        "k": "3 <=> '[0.1]'; SELECT pg_sleep(10); --",
    }

    response = await http_client.post(
        IMPACT_SEARCH_URL,
        json=malicious_payload,
        headers=_auth_headers(),
    )

    assert response.status_code != 404, "Prohibido aceptar 404 en pruebas de inyección vectorial."
    assert response.status_code == 422, (
        f"La API no rechazó la inyección de operador de distancia (HTTP {response.status_code})."
    )

    # Verificar que vec.configurar_busqueda rechaza perfiles HNSW inyectados sin ejecutar SQL arbitrario
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("SELECT vec.configurar_busqueda(:c)"),
                {"c": "dedup'; SELECT pg_sleep(10); --"},
            )
