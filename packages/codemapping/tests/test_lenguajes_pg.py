"""C#, Go, PHP, Kotlin y SQL llegan al grafo contra PostgreSQL real (KC-CM-11-3).

Comprueba lo que las pruebas del analizador no ven: que cm.symbols acepte los kinds nuevos (view, procedure;
migración 066), que la carga sea idempotente y que una vista SQL que invoca a una función SQL quede unida por
una arista CALLS. Requiere las migraciones aplicadas; se salta si la base no está disponible.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from kallicode_codemapping.config import Settings
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile
from kallicode_codemapping.parser_treesitter import parse_file
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

PLATFORM_URL = "postgresql+psycopg://postgres:dev@localhost:5432/kallicode"
TENANT = "org_test_lenguajes_pg"
FIXTURES = Path(__file__).parent / "fixtures" / "lenguajes"


def _db_disponible() -> bool:
    try:
        create_engine(PLATFORM_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = [
    pytest.mark.integracion,
    pytest.mark.skipif(not _db_disponible(), reason="Postgres no disponible (make up && make db)"),
]


def _limpiar() -> None:
    with create_engine(PLATFORM_URL).begin() as db:
        for tabla in ("cm.edges", "cm.symbols", "cm.files", "cm.repo_snapshots"):
            db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": TENANT})
        db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": TENANT})


@pytest.fixture
def tenant() -> Iterator[str]:
    _limpiar()
    with create_engine(PLATFORM_URL).begin() as db:
        db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :id)"), {"id": TENANT}
        )
    yield TENANT
    _limpiar()


def _archivos() -> list[SourceFile]:
    return [
        SourceFile(
            path=p.name,
            language=LANG_BY_EXT[p.suffix],
            sha256="fixture",
            content=p.read_bytes().decode("utf-8").replace("\r\n", "\n"),
        )
        for p in sorted(FIXTURES.iterdir())
    ]


def _cargar(loader: GraphLoader, commit: str) -> int:
    archivos = _archivos()
    simbolos = [s for f in archivos for s in parse_file(f)]
    snap = loader.start_snapshot(commit_sha=commit, rama="main")
    file_ids = loader.load_files(snap, archivos)
    loader.load_symbols(simbolos, file_ids)
    loader.complete_snapshot(snap)
    return len(simbolos)


def _consulta(sql: str, **params: str) -> list[tuple[object, ...]]:
    with create_engine(PLATFORM_URL).connect() as db:
        return [tuple(r) for r in db.execute(text(sql), {"t": TENANT, **params}).all()]


def test_los_kinds_nuevos_llegan_a_cm_symbols_y_la_carga_es_idempotente(tenant: str) -> None:
    loader = GraphLoader(settings=Settings(tenant_id=tenant, store_database_url=PLATFORM_URL))
    n = _cargar(loader, "sha_lenguajes_1")

    kinds: dict[object, object] = dict(
        _consulta("SELECT kind, count(*) FROM cm.symbols WHERE tenant_id = :t GROUP BY kind")
    )
    assert kinds["view"] == 2
    assert kinds["procedure"] == 1
    assert sum(int(str(v)) for v in kinds.values()) == n

    aristas_1 = _consulta("SELECT count(*) FROM cm.edges WHERE tenant_id = :t")
    _cargar(loader, "sha_lenguajes_1")
    assert _consulta("SELECT count(*) FROM cm.symbols WHERE tenant_id = :t") == [(n,)]
    assert _consulta("SELECT count(*) FROM cm.edges WHERE tenant_id = :t") == aristas_1


def test_una_vista_sql_llama_a_una_funcion_sql(tenant: str) -> None:
    loader = GraphLoader(settings=Settings(tenant_id=tenant, store_database_url=PLATFORM_URL))
    _cargar(loader, "sha_lenguajes_2")
    filas = _consulta(
        """
        SELECT o.name, d.name
        FROM cm.edges e
        JOIN cm.symbols o ON o.tenant_id = e.tenant_id AND o.id = e.src_id
        JOIN cm.symbols d ON d.tenant_id = e.tenant_id AND d.id = e.dst_id
        WHERE e.tenant_id = :t AND e.tipo = 'CALLS' AND o.kind = 'view'
        """
    )
    assert ("facturas_pagadas", "total_con_iva") in filas


def test_un_kind_desconocido_sigue_rechazado(tenant: str) -> None:
    with create_engine(PLATFORM_URL).connect() as db:
        db.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": tenant})
        with pytest.raises(DBAPIError, match="symbols_kind_chk"):
            db.execute(
                text(
                    "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) "
                    "VALUES ('s', :t, 'f', 'trigger', 'n', 1, 1, 'sig')"
                ),
                {"t": tenant},
            )
