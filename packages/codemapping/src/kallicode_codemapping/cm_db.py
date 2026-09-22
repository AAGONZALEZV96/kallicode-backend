"""Acceso a PostgreSQL para el esquema cm (KC-ONB-03-1).

Motor propio, separado del de la API/workers: CodeMapping corre como
pipeline batch independiente (`build_graph.py`), que es sincrono de punta
a punta, así que esta capa lo es también -- a diferencia de
kallicode_core.db, que es async porque sirve peticiones de FastAPI.

`SET LOCAL app.tenant_id` activa las políticas de RLS creadas en
db/migrations/002_esquema_cm.sql (mismo patrón que db/04_rls.sql): la
sesión queda confinada al tenant activo y una fila ajena es indistinguible
de una inexistente.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engines: dict[str, Engine] = {}


def _engine_for(platform_database_url: str) -> Engine:
    if platform_database_url not in _engines:
        _engines[platform_database_url] = create_engine(
            platform_database_url, pool_size=5, max_overflow=10, pool_pre_ping=True
        )
    return _engines[platform_database_url]


@contextmanager
def sesion_tenant(platform_database_url: str, tenant_id: str) -> Iterator[Session]:
    """Sesión transaccional confinada al tenant activo (RLS de cm.*)."""
    maker = sessionmaker(bind=_engine_for(platform_database_url))
    with maker() as db, db.begin():
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        yield db


def ejecutar(db: Session, sql: str, params: dict[str, Any] | None = None) -> None:
    db.execute(text(sql), params or {})


def ejecutar_lote(db: Session, sql: str, filas: list[dict[str, Any]]) -> None:
    """INSERT/UPDATE por lotes. No hace nada si `filas` está vacío (SQLAlchemy
    no acepta executemany con una lista vacía de parámetros)."""
    if not filas:
        return
    db.execute(text(sql), filas)


def uno(db: Session, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    fila = db.execute(text(sql), params or {}).mappings().first()
    return dict(fila) if fila else None


def todos(db: Session, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(f) for f in db.execute(text(sql), params or {}).mappings().all()]
