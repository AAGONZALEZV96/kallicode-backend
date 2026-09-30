import pytest
from kallicode_core.comercial import _consumir
from kallicode_core.errors import AppError

pytestmark = pytest.mark.asyncio


async def test_redis_doble_disponible_en_tests_de_api(redis_doble):
    await _consumir("rl:api:prueba", 2)
    await _consumir("rl:api:prueba", 2)

    with pytest.raises(AppError):
        await _consumir("rl:api:prueba", 2)

    assert await redis_doble.get("rl:api:prueba") == "3"
