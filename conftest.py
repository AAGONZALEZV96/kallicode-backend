"""Fixtures comunes para aislar los engines async entre event loops."""

import kallicode_core.db as db_module
import pytest
from kallicode_core import comercial


@pytest.fixture(autouse=True)
async def reset_db_engines_between_tests():
    yield

    if db_module._engine_app is not None:
        await db_module._engine_app.dispose()
    if db_module._engine_admin is not None:
        await db_module._engine_admin.dispose()
    if comercial._redis is not None:
        await comercial._redis.aclose()

    db_module._engine_app = None
    db_module._engine_admin = None
    db_module._sessionmaker_app = None
    db_module._sessionmaker_admin = None
    comercial._redis = None
