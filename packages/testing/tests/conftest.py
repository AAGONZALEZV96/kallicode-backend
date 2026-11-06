"""Configuración global de pytest y fixtures para el monorepo KalliCode."""

import os
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from packages.testing.tests.fixtures.vcr_llm_fixtures import mock_llm_endpoints

__all__ = ["mock_llm_endpoints"]

# Usa la configuración del proyecto: variables de entorno en CI y .env en local.
from kallicode_core.config import get_settings

_settings = get_settings()

DATABASE_URL = _settings.database_url

os.environ["KC_DATABASE_URL"] = DATABASE_URL
os.environ["KC_DATABASE_URL_ADMIN"] = _settings.database_url_admin
os.environ["KC_REDIS_URL"] = _settings.redis_url

if _settings.redis_password:
    os.environ["KC_REDIS_PASSWORD"] = _settings.redis_password

engine = create_async_engine(DATABASE_URL, echo=False)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provee una sesión aislada con rollback automático (SAVEPOINT).
    Evita la contaminación de la base de datos entre pruebas.
    Cualquier commit explícito en el test se confina al savepoint.
    """
    async with engine.connect() as conn:
        # Iniciar transacción primaria
        await conn.begin()
        # Iniciar transacción anidada (SAVEPOINT)
        await conn.begin_nested()

        async_session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )

        yield async_session

        await async_session.close()
        # Descartar todos los cambios al finalizar el test
        await conn.rollback()


@pytest.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provee un cliente HTTP asíncrono y aislado para pruebas de integración FastAPI."""
    async with httpx.AsyncClient(base_url="http://testserver") as client:
        yield client


@pytest.fixture
def deterministic_uuid() -> uuid.UUID:
    """Genera un UUID predecible para pruebas de seguridad y validación de semillas."""
    return uuid.UUID("00000000-0000-4000-8000-000000000001")
