"""Pruebas de monotonicidad, desbordamiento y concurrencia para ULID (KC-BE-10-5)."""

from __future__ import annotations

import asyncio
import itertools
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from kallicode_core import ids


@pytest.fixture(autouse=True)
def _reiniciar_estado_ulid():
    with ids._lock:
        ids._ultimo_ms = -1
        ids._ultimo_rnd = 0
    yield
    with ids._lock:
        ids._ultimo_ms = -1
        ids._ultimo_rnd = 0


def test_diez_mil_identificadores_estrictamente_crecientes() -> None:
    generados = [ids.nuevo("ev") for _ in range(10_000)]

    assert len(generados) == 10_000
    assert len(set(generados)) == 10_000
    for anterior, actual in itertools.pairwise(generados):
        assert actual > anterior
        assert actual.startswith("ev_")
        assert len(actual) == 29  # 2 prefijo + 1 '_' + 26 ULID


def test_mismo_milisegundo_y_desbordamiento_sin_colision() -> None:
    ms_fijo = 1_764_000_000_000
    with patch("kallicode_core.ids.time.time_ns", return_value=ms_fijo * 1_000_000):
        primero = ids._ulid()
        segundo = ids._ulid()
        assert segundo > primero
        assert primero[:10] == segundo[:10]

        # Forzamos el borde superior de los 80 bits para verificar que el
        # desbordamiento avanza el timestamp en +1 ms sin colisionar ni envolver.
        with ids._lock:
            ids._ultimo_rnd = ids._MAX_ALEATORIO

        desbordado = ids._ulid()
        assert desbordado > segundo
        assert desbordado[:10] > segundo[:10]


@pytest.mark.asyncio
async def test_generacion_concurrente_sin_duplicados() -> None:
    tareas = 20
    por_tarea = 500

    async def trabajador() -> list[str]:
        await asyncio.sleep(0)
        return [ids.nuevo("aud") for _ in range(por_tarea)]

    lotes = await asyncio.gather(*(trabajador() for _ in range(tareas)))
    todos = [identificador for lote in lotes for identificador in lote]

    assert len(todos) == tareas * por_tarea
    assert len(set(todos)) == tareas * por_tarea

    # También verificamos concurrencia real entre hilos del proceso.
    def trabajador_hilo(_: int) -> list[str]:
        return [ids.nuevo("thr") for _ in range(250)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        lotes_hilos = list(pool.map(trabajador_hilo, range(8)))

    todos_hilos = [identificador for lote in lotes_hilos for identificador in lote]
    assert len(todos_hilos) == 2_000
    assert len(set(todos_hilos)) == 2_000
