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

    Cubre: get, set, setex, incr, expire, ttl, delete, exists, ping y xadd.
    Otros comandos de streams (xack, xautoclaim, xgroup_create, xreadgroup) pendientes.
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

    async def xadd(self, stream, fields, *args, **kwargs):
        clave = self._k(f"stream:{stream}")
        entrada = self._data.get(clave, "")
        entrada_id = f"{len(entrada.split('|')) if entrada else 0}-0"
        registro = "|".join(f"{key}={value}" for key, value in fields.items())
        self._data[clave] = (
            f"{entrada}|{entrada_id}:{registro}" if entrada else f"{entrada_id}:{registro}"
        )
        return entrada_id

    async def eval(self, script, numkeys, *keys_and_args):
        import math

        numkeys = int(numkeys)
        clave = self._k(str(keys_and_args[0]))
        capacidad = float(keys_and_args[numkeys])
        ventana = float(keys_and_args[numkeys + 1])
        ahora = float(keys_and_args[numkeys + 2])
        costo = float(keys_and_args[numkeys + 3])
        ttl_ms = int(keys_and_args[numkeys + 4])

        tasa = capacidad / ventana
        raw_tokens = self._data.get(f"{clave}:tokens")
        raw_ts = self._data.get(f"{clave}:ts")

        if raw_tokens is None or raw_ts is None:
            tokens = capacidad
            ultimo_ts = ahora
        else:
            tokens = float(raw_tokens)
            ultimo_ts = float(raw_ts)
            transcurrido = max(0.0, ahora - ultimo_ts)
            tokens = min(capacidad, tokens + transcurrido * tasa)

        permitido = 0
        retry_after = 0
        if tokens >= costo:
            tokens -= costo
            permitido = 1
        else:
            deficit = costo - tokens
            retry_after = max(1, math.ceil(deficit / tasa))

        restante = max(0, math.floor(tokens + 1e-9))
        reinicio_s = 0 if tokens >= capacidad else max(1, math.ceil((capacidad - tokens) / tasa))

        self._data[f"{clave}:tokens"] = str(tokens)
        self._data[f"{clave}:ts"] = str(ahora)
        self._ttls[clave] = max(1, math.ceil(ttl_ms / 1000))
        return [permitido, int(capacidad), restante, reinicio_s, retry_after]

    async def aclose(self):
        self._data.clear()
        self._ttls.clear()


_DESTINOS_REDIS = (
    "kallicode_api.servicios.boveda",
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
