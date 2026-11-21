import pytest
from kallicode_core.comercial import _consumir
from kallicode_core.errors import AppError

pytestmark = pytest.mark.asyncio


async def test_redis_doble_disponible_en_tests_de_api(redis_doble):
    e1 = await _consumir("rl:api:prueba", 2)
    assert e1.restante == 1
    e2 = await _consumir("rl:api:prueba", 2)
    assert e2.restante == 0

    with pytest.raises(AppError):
        await _consumir("rl:api:prueba", 2)
