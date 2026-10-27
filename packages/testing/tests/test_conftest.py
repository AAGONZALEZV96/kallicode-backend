"""Pruebas unitarias para validar las fixtures globales de QA."""

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_fixture_deterministic_uuid(deterministic_uuid: uuid.UUID) -> None:
    """Verifica la correcta generación de semillas estáticas."""
    assert str(deterministic_uuid) == "00000000-0000-4000-8000-000000000001"


async def test_fixture_http_client(http_client: httpx.AsyncClient) -> None:
    """Verifica la inicialización del cliente HTTP aislado."""
    assert isinstance(http_client, httpx.AsyncClient)
    assert http_client.base_url == httpx.URL("http://testserver")


async def test_fixture_db_session_structure(db_session: AsyncSession) -> None:
    """Verifica que la sesión se entregue activa y lista para el test."""
    assert isinstance(db_session, AsyncSession)
    assert db_session.is_active is True
