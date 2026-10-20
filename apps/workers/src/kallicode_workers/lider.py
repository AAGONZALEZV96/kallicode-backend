"""Elección de líder para trabajos programados.

Los programados (scheduler, billing_cycle, housekeeping) corren en todas
las réplicas del contenedor de workers. `pg_advisory_lock` garantiza que
solo una réplica ejecuta cada ciclo a la vez.

El lock es session-scoped: se libera al cerrar la conexión (o al hacer
`pg_advisory_unlock`). Si el proceso muere, PostgreSQL lo libera solo.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from kallicode_core.db import sesion_sistema, uno
from kallicode_core.logging import log
from sqlalchemy import text


@contextlib.asynccontextmanager
async def con_lider(nombre: str) -> AsyncIterator[bool]:
    """Cede `True` si esta réplica consiguió el lock `nombre`; `False` si
    otra lo tiene (no bloquea, no espera turno).

    Uso:
        async with con_lider("billing_cycle") as es_lider:
            if not es_lider:
                return
            await ciclo()
    """
    async with sesion_sistema() as db:
        row = await uno(
            db,
            "SELECT pg_try_advisory_lock(hashtext(:n)) AS ok",
            {"n": nombre},
        )
        es_lider = bool(row["ok"])
        if es_lider:
            log.debug("lider.adquirido", nombre=nombre)
        else:
            log.debug("lider.ocupado", nombre=nombre)
        try:
            yield es_lider
        finally:
            if es_lider:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:n))"),
                        {"n": nombre},
                    )
                    log.debug("lider.liberado", nombre=nombre)
                except Exception:
                    log.warning("lider.unlock_fallido", nombre=nombre, exc_info=True)
