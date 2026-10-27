import pytest
from kallicode_core.comercial import _consumir
from kallicode_core.errors import AppError

pytestmark = pytest.mark.asyncio


async def test_rate_limit_bloquea_al_superar_limite(redis_doble):
    clave = "rl:test:usuario1"
    limite = 3

    # Las primeras 3 llamadas pasan sin problema
    for _ in range(limite):
        await _consumir(clave, limite)

    # La cuarta debe lanzar el error de rate limit
    with pytest.raises(AppError):
        await _consumir(clave, limite)


async def test_namespace_aisla_entre_tests(redis_doble):
    valor = await redis_doble.get("clave_inexistente")
    assert valor is None

    await redis_doble.set("clave_inexistente", "valor")
    valor = await redis_doble.get("clave_inexistente")
    assert valor == "valor"


async def test_ttl_y_expire_como_redis_real(redis_doble):
    # Clave inexistente: Redis real devuelve -2 en ttl y False en expire
    assert await redis_doble.ttl("no_existe") == -2
    assert await redis_doble.expire("no_existe", 60) is False

    # Clave sin expiración: ttl devuelve -1
    await redis_doble.set("k", "v")
    assert await redis_doble.ttl("k") == -1

    # Con expiración: ttl devuelve los segundos fijados
    await redis_doble.expire("k", 60)
    assert await redis_doble.ttl("k") == 60
