"""Fixtures comunes para aislar los engines async entre event loops."""

import importlib

import kallicode_core.db as db_module
import pytest
import pytest_asyncio
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


class RedisDoble:
    """Doble en memoria de un cliente Redis async, con namespace por prueba.

    Cubre: get, set, setex, incr, expire, ttl, delete, exists, ping.
    Streams (xadd, xack, xautoclaim, xgroup_create, xreadgroup) pendientes.
    """

    def __init__(self, namespace: str):
        self._ns = namespace
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def _k(self, clave: str) -> str:
        return f"{self._ns}:{clave}"

    async def get(self, clave):
        return self._data.get(self._k(clave))

    async def mget(self, *claves):
        return [self._data.get(self._k(c)) for c in claves]

    async def set(self, clave, valor, ex=None):
        self._data[self._k(clave)] = valor
        if ex is not None:
            self._ttls[self._k(clave)] = ex
        return True

    async def setex(self, clave, tiempo, valor):
        return await self.set(clave, valor, ex=tiempo)

    async def incr(self, clave):
        k = self._k(clave)
        actual = int(self._data.get(k, 0)) + 1
        self._data[k] = str(actual)
        return actual

    async def expire(self, clave, segundos):
        k = self._k(clave)
        if k not in self._data:
            return False
        self._ttls[k] = segundos
        return True

    async def ttl(self, clave):
        k = self._k(clave)
        if k not in self._data:
            return -2
        return self._ttls.get(k, -1)

    async def delete(self, *claves):
        borrados = 0
        for c in claves:
            k = self._k(c)
            if k in self._data:
                del self._data[k]
                self._ttls.pop(k, None)
                borrados += 1
        return borrados

    async def exists(self, clave):
        return 1 if self._k(clave) in self._data else 0

    async def ping(self):
        return True

    async def aclose(self):
        self._data.clear()
        self._ttls.clear()


_DESTINOS_REDIS = (
    "kallicode_api.servicios.boveda",
    "kallicode_workers.billing_cycle",
    "kallicode_workers.normalizer",
    "kallicode_workers.main",
)


@pytest_asyncio.fixture
async def redis_doble(request, monkeypatch):
    """Reemplaza redis_cliente() por un doble en memoria, aislado por test."""
    doble = RedisDoble(f"test:{request.node.name}")
    fabrica = lambda: doble

    monkeypatch.setattr(comercial, "redis_cliente", fabrica)
    for nombre in _DESTINOS_REDIS:
        try:
            modulo = importlib.import_module(nombre)
        except ImportError:
            continue
        monkeypatch.setattr(modulo, "redis_cliente", fabrica)

    yield doble
    await doble.aclose()


def pytest_collection_modifyitems(items):
    """Los tests de apps/api/tests usan la app y la BD reales: van al job de integración."""
    for item in items:
        if "apps/api/tests" in item.nodeid:
            item.add_marker(pytest.mark.integracion)
