"""Configuración global de pytest y fixtures para el monorepo KalliCode."""

import os
import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# El pipeline CI inyectará KC_DATABASE_URL con la contraseña 'dev'.
# En local, usa la cadena definida por el equipo de base de datos.
DATABASE_URL = os.getenv(
    "KC_DATABASE_URL",
    "postgresql+asyncpg://kallicode_app:KalliCode_Dev2026!@localhost:5432/kallicode_test",  # pragma: allowlist secret
)
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
