"""Pruebas de integración de GraphLoader contra PostgreSQL real (KC-ONB-03-1).

Requieren Postgres levantado y el esquema cm aplicado:
    make up && make db

Se saltan automáticamente si la base no está disponible.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from kallicode_codemapping.config import Settings
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile
from kallicode_codemapping.parser_treesitter import Symbol, parse_file
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

PLATFORM_URL = "postgresql+psycopg://postgres:dev@localhost:5432/kallicode"
TENANT_A = "org_test_graph_loader_a"
TENANT_B = "org_test_graph_loader_b"


def _db_disponible() -> bool:
    try:
        create_engine(PLATFORM_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_disponible(), reason="Postgres no disponible (make up && make db)")


def _limpiar_tenant(tenant_id: str) -> None:
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        for tabla in (
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.db_columns",
            "cm.db_tables",
            "cm.definitions",
            "cm.concepts",
            "cm.curation_queue",
            "cm.nuggets",
            "cm.incidents",
            "cm.repo_snapshots",
        ):
            db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": tenant_id})
        db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})


@pytest.fixture
def dos_tenants() -> Iterator[tuple[str, str]]:
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        for t in (TENANT_A, TENANT_B):
            db.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :nombre)"),
                {"id": t, "nombre": f"Tenant de prueba {t}"},
            )
    yield TENANT_A, TENANT_B
    _limpiar_tenant(TENANT_A)
    _limpiar_tenant(TENANT_B)


def _repo_fixture() -> list[SourceFile]:
    import os

    raiz = os.path.join(os.path.dirname(__file__), "fixtures", "repo_ref")
    archivos: list[SourceFile] = []
    for dirpath, _dirnames, filenames in os.walk(raiz):
        for nombre in filenames:
            ext = os.path.splitext(nombre)[1]
            if ext not in LANG_BY_EXT:
                continue
            ruta = os.path.join(dirpath, nombre)
            with open(ruta, encoding="utf-8", errors="replace") as fh:
                contenido = fh.read()
            archivos.append(
                SourceFile(
                    path=os.path.relpath(ruta, raiz),
                    language=LANG_BY_EXT[ext],
                    sha256="fixture",
                    content=contenido,
                )
            )
    return archivos


def _parsear(archivos: list[SourceFile]) -> list[Symbol]:
    return [sym for f in archivos for sym in parse_file(f)]


def _contar(db_url: str, tabla: str, tenant_id: str) -> int:
    engine = create_engine(db_url)
    with engine.connect() as db:
        return int(
            db.execute(text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :t"), {"t": tenant_id}).scalar_one()
        )


def test_cargar_dos_veces_es_idempotente(dos_tenants: tuple[str, str]) -> None:
    """Criterio central de KC-ONB-03-1: cargar el mismo commit dos veces no
    duplica nodos ni aristas (CM-F09)."""
    tenant_a, _ = dos_tenants
    settings = Settings(tenant_id=tenant_a, store_database_url=PLATFORM_URL)
    loader = GraphLoader(settings=settings)

    archivos = _repo_fixture()
    simbolos = _parsear(archivos)
    assert archivos and simbolos, "el fixture del repo de referencia no debe estar vacío"

    snap1 = loader.start_snapshot(commit_sha="sha_test_1", rama="main")
    file_ids = loader.load_files(snap1, archivos)
    loader.load_symbols(simbolos, file_ids)
    loader.complete_snapshot(snap1)

    n_files_1 = _contar(PLATFORM_URL, "cm.files", tenant_a)
    n_symbols_1 = _contar(PLATFORM_URL, "cm.symbols", tenant_a)
    n_edges_1 = _contar(PLATFORM_URL, "cm.edges", tenant_a)
    assert n_files_1 == len(archivos)
    assert n_symbols_1 == len(simbolos)

    # Segunda carga del MISMO contenido: mismos recuentos, ningún duplicado.
    snap2 = loader.start_snapshot(commit_sha="sha_test_1", rama="main")
    file_ids_2 = loader.load_files(snap2, archivos)
    loader.load_symbols(simbolos, file_ids_2)
    loader.complete_snapshot(snap2)

    assert _contar(PLATFORM_URL, "cm.files", tenant_a) == n_files_1
    assert _contar(PLATFORM_URL, "cm.symbols", tenant_a) == n_symbols_1
    assert _contar(PLATFORM_URL, "cm.edges", tenant_a) == n_edges_1
    # Los ids deterministas son iguales entre corridas (no dependen de hash()/PYTHONHASHSEED).
    assert file_ids == file_ids_2
    # El snapshot sí es una fila nueva por cada corrida.
    assert _contar(PLATFORM_URL, "cm.repo_snapshots", tenant_a) == 2


def test_dos_tenants_con_mismo_archivo_no_colisionan(dos_tenants: tuple[str, str]) -> None:
    """CM-F01: dos tenants con el mismo path/nombre no comparten nodo."""
    tenant_a, tenant_b = dos_tenants
    archivos = _repo_fixture()[:1]  # un solo archivo alcanza para la prueba
    simbolos = _parsear(archivos)

    for tenant in (tenant_a, tenant_b):
        settings = Settings(tenant_id=tenant, store_database_url=PLATFORM_URL)
        loader = GraphLoader(settings=settings)
        snap = loader.start_snapshot(commit_sha="sha_colision", rama="main")
        file_ids = loader.load_files(snap, archivos)
        loader.load_symbols(simbolos, file_ids)
        loader.complete_snapshot(snap)

    assert _contar(PLATFORM_URL, "cm.files", tenant_a) == 1
    assert _contar(PLATFORM_URL, "cm.files", tenant_b) == 1
    # Cuatro filas en total (una por tenant), no dos compartidas.
    engine = create_engine(PLATFORM_URL)
    with engine.connect() as db:
        total: int = db.execute(
            text("SELECT count(*) FROM cm.files WHERE tenant_id IN (:a, :b)"),
            {"a": tenant_a, "b": tenant_b},
        ).scalar_one()
    assert total == 2


def test_rls_bloquea_lectura_cruzada_entre_tenants(dos_tenants: tuple[str, str]) -> None:
    """La política tenant_isolation de db/migrations/002_esquema_cm.sql debe
    impedir que una sesión confinada al tenant B vea filas del tenant A --
    conectando como el rol de aplicación (no como superusuario, que
    siempre pasa por encima de RLS)."""
    tenant_a, tenant_b = dos_tenants
    settings = Settings(tenant_id=tenant_a, store_database_url=PLATFORM_URL)
    loader = GraphLoader(settings=settings)
    archivos = _repo_fixture()[:1]
    snap = loader.start_snapshot(commit_sha="sha_rls", rama="main")
    loader.load_files(snap, archivos)
    loader.complete_snapshot(snap)

    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(text("SET ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
        visibles: int = db.execute(text("SELECT count(*) FROM cm.files")).scalar_one()
        db.execute(text("RESET ROLE"))
    assert visibles == 0, "el tenant B no debe ver filas del tenant A bajo RLS"


def test_calls_no_cruza_lenguajes(dos_tenants: tuple[str, str]) -> None:
    """Dos símbolos homónimos en lenguajes distintos no deben enlazarse por
    CALLS -- el nombre solo no alcanza para resolver la llamada."""
    tenant_a, _ = dos_tenants
    settings = Settings(tenant_id=tenant_a, store_database_url=PLATFORM_URL)
    loader = GraphLoader(settings=settings)

    archivos = [
        SourceFile(path="a.py", language="python", sha256="x", content=""),
        SourceFile(path="b.js", language="javascript", sha256="x", content=""),
    ]
    simbolos = [
        Symbol(
            id="sym_caller",
            file="a.py",
            language="python",
            kind="function",
            name="caller",
            start_line=1,
            end_line=2,
            signature="def caller()",
            snippet="",
            calls=["helper"],
        ),
        Symbol(
            id="sym_helper_py",
            file="a.py",
            language="python",
            kind="function",
            name="helper",
            start_line=3,
            end_line=4,
            signature="def helper()",
            snippet="",
        ),
        Symbol(
            id="sym_helper_js",
            file="b.js",
            language="javascript",
            kind="function",
            name="helper",
            start_line=1,
            end_line=2,
            signature="function helper()",
            snippet="",
        ),
    ]

    snap = loader.start_snapshot(commit_sha="sha_calls", rama="main")
    file_ids = loader.load_files(snap, archivos)
    loader.load_symbols(simbolos, file_ids)
    loader.complete_snapshot(snap)

    engine = create_engine(PLATFORM_URL)
    with engine.connect() as db:
        destinos: list[str] = (
            db.execute(
                text("SELECT dst_id FROM cm.edges WHERE tenant_id = :t AND tipo = 'CALLS' AND src_id = 'sym_caller'"),
                {"t": tenant_a},
            )
            .scalars()
            .all()
        )
    assert destinos == ["sym_helper_py"], "CALLS debe resolver al símbolo del mismo lenguaje, no al homónimo en JS"


def test_load_schema_y_load_semantic(dos_tenants: tuple[str, str]) -> None:
    """Ejercita las tablas cliente (db_tables/db_columns) y la capa semántica
    (definitions/concepts) con sus aristas -- caminos que no comparte código
    con load_files/load_symbols."""
    tenant_a, _ = dos_tenants
    settings = Settings(tenant_id=tenant_a, store_database_url=PLATFORM_URL)
    loader = GraphLoader(settings=settings)

    archivos = _repo_fixture()[:1]
    snap = loader.start_snapshot(commit_sha="sha_schema", rama="main")
    file_ids = loader.load_files(snap, archivos)

    tables = [{"name": "invoices", "columns": [{"name": "id", "type": "text", "nullable": False}]}]
    fks: list[dict[str, str]] = []
    usage = [{"file": archivos[0].path, "table": "invoices"}]
    tabla_ids = loader.load_schema(tables, fks, usage, file_ids)
    assert tabla_ids["invoices"]
    assert _contar(PLATFORM_URL, "cm.db_tables", tenant_a) == 1
    assert _contar(PLATFORM_URL, "cm.db_columns", tenant_a) == 1

    definitions = [
        {
            "id": "def-1",
            "name_en": "Invoice creation",
            "kind": "capability",
            "description_en": "Crea una factura",
            "module": "billing",
            "member_symbol_ids": [],
        }
    ]
    ontology = {
        "concepts": [
            {"name_en": "Invoice", "name_local": "Factura", "description_en": "Una factura", "confidence": 0.9}
        ],
        "relations": [],
    }
    links = [{"definition_id": "def-1", "concept_name_en": "Invoice", "confidence": 0.8}]
    loader.load_semantic(definitions, ontology, links)

    assert _contar(PLATFORM_URL, "cm.definitions", tenant_a) == 1
    assert _contar(PLATFORM_URL, "cm.concepts", tenant_a) == 1

    engine = create_engine(PLATFORM_URL)
    with engine.connect() as db:
        expresa: int = db.execute(
            text("SELECT count(*) FROM cm.edges WHERE tenant_id = :t AND tipo = 'EXPRESSES'"),
            {"t": tenant_a},
        ).scalar_one()
        usa_tabla: int = db.execute(
            text("SELECT count(*) FROM cm.edges WHERE tenant_id = :t AND tipo = 'USES_TABLE'"),
            {"t": tenant_a},
        ).scalar_one()
    assert expresa == 1
    assert usa_tabla == 1

    loader.complete_snapshot(snap)


def test_get_last_sha_y_delete_file_subgraph(dos_tenants: tuple[str, str]) -> None:
    tenant_a, _ = dos_tenants
    settings = Settings(tenant_id=tenant_a, store_database_url=PLATFORM_URL)
    loader = GraphLoader(settings=settings)

    assert loader.get_last_sha() is None  # sin snapshots completos todavía

    archivos = _repo_fixture()[:1]
    simbolos = _parsear(archivos)
    snap = loader.start_snapshot(commit_sha="sha_delete", rama="main")
    file_ids = loader.load_files(snap, archivos)
    loader.load_symbols(simbolos, file_ids)
    loader.complete_snapshot(snap)

    assert loader.get_last_sha() == "sha_delete"
    assert _contar(PLATFORM_URL, "cm.files", tenant_a) == 1
    assert _contar(PLATFORM_URL, "cm.symbols", tenant_a) == len(simbolos)

    loader.delete_file_subgraph([archivos[0].path])

    assert _contar(PLATFORM_URL, "cm.files", tenant_a) == 0
    assert _contar(PLATFORM_URL, "cm.symbols", tenant_a) == 0  # cascada desde files
    assert _contar(PLATFORM_URL, "cm.edges", tenant_a) == 0  # limpiadas a mano
