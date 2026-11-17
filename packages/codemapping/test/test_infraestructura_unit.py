"""Pruebas unitarias de cm_db, checkpoints, logging_setup, embeddings y extract_sql (KC-CM-06-6).

Todas corren sin red ni Postgres: SQLAlchemy y httpx se sustituyen por dobles.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from kallicode_codemapping import checkpoints, cm_db, embeddings, extract_sql
from kallicode_codemapping.config import build_settings
from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.logging_setup import JsonFormatter, get_logger

# --------------------------------------------------------------------------
# checkpoints
# --------------------------------------------------------------------------


def test_checkpoints_guardar_cargar_y_limpiar(tmp_path: Path) -> None:
    datos = {"files": [1, 2], "tables": []}
    checkpoints.guardar(str(tmp_path), "t1", "s1", "ingesta", datos)

    assert checkpoints.cargar(str(tmp_path), "t1", "s1", "ingesta") == datos
    checkpoints.limpiar(str(tmp_path), "t1", "s1")
    assert checkpoints.cargar(str(tmp_path), "t1", "s1", "ingesta") is None


def test_checkpoints_cargar_inexistente_devuelve_none(tmp_path: Path) -> None:
    assert checkpoints.cargar(str(tmp_path), "t1", "s1", "nada") is None


def test_checkpoints_ignora_contenido_que_no_es_un_objeto(tmp_path: Path) -> None:
    checkpoints.guardar(str(tmp_path), "t1", "s1", "ingesta", {})
    ruta = tmp_path / "t1" / "checkpoints" / "s1" / "ingesta.json"
    ruta.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert checkpoints.cargar(str(tmp_path), "t1", "s1", "ingesta") is None


def test_checkpoints_aislan_tenants_y_snapshots(tmp_path: Path) -> None:
    checkpoints.guardar(str(tmp_path), "t1", "s1", "ingesta", {"v": 1})
    checkpoints.guardar(str(tmp_path), "t2", "s1", "ingesta", {"v": 2})
    checkpoints.limpiar(str(tmp_path), "t1", "s1")
    assert checkpoints.cargar(str(tmp_path), "t2", "s1", "ingesta") == {"v": 2}


def test_checkpoints_limpiar_sin_cache_no_falla(tmp_path: Path) -> None:
    checkpoints.limpiar(str(tmp_path), "t1", "inexistente")


# --------------------------------------------------------------------------
# logging_setup
# --------------------------------------------------------------------------


def _registro(**extra: Any) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "hola %s", ("mundo",), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_formatter_emite_campos_base() -> None:
    salida = json.loads(JsonFormatter().format(_registro()))
    assert salida["level"] == "WARNING"
    assert salida["message"] == "hola mundo"
    assert "timestamp" in salida
    assert "trace_id" not in salida


def test_json_formatter_incluye_contexto_y_campos_extra() -> None:
    rec = _registro(
        trace_id="tr", tenant_id="t1", phase="3/7", extra_fields={"files": 4, "nota": "ñandú"}
    )
    salida = json.loads(JsonFormatter().format(rec))
    assert (salida["trace_id"], salida["tenant_id"], salida["phase"]) == ("tr", "t1", "3/7")
    assert salida["files"] == 4
    assert salida["nota"] == "ñandú"


def test_get_logger_configura_un_unico_handler_json() -> None:
    nombre = "codemapping.test.unico"
    logging.getLogger(nombre).handlers.clear()
    a = get_logger(nombre)
    b = get_logger(nombre)
    assert a is b
    assert len(a.handlers) == 1
    assert isinstance(a.handlers[0].formatter, JsonFormatter)
    assert a.level == logging.INFO


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


def test_uuid5_es_determinista_y_depende_de_tenant_capa_y_nodo() -> None:
    base = embeddings.generar_uuid5_vector("t", "code", "n")
    assert base == embeddings.generar_uuid5_vector("t", "code", "n")
    assert base != embeddings.generar_uuid5_vector("t2", "code", "n")
    assert base != embeddings.generar_uuid5_vector("t", "docs", "n")
    assert base != embeddings.generar_uuid5_vector("t", "code", "m")


def test_contenido_hash_es_sha256() -> None:
    assert embeddings.calcular_contenido_hash("a") == (
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )
    assert embeddings.calcular_contenido_hash("a") != embeddings.calcular_contenido_hash("b")


def test_preparar_items_vectoriales_usa_node_ref_o_id() -> None:
    items = [
        {"node_ref": "n1", "text": "uno", "payload": {"k": 1}},
        {"id": "n2", "text": "dos"},
    ]
    res = embeddings.preparar_items_vectoriales("t", "code", items)
    assert [r["node_ref"] for r in res] == ["n1", "n2"]
    assert res[0]["payload"] == {"k": 1}
    assert res[1]["payload"] == {}
    assert res[0]["id"] == str(embeddings.generar_uuid5_vector("t", "code", "n1"))
    assert "vector" not in res[0]


def _transporte(captura: dict[str, Any]) -> httpx.MockTransport:
    def manejador(request: httpx.Request) -> httpx.Response:
        captura["url"] = str(request.url)
        captura["cuerpo"] = json.loads(request.content)
        return httpx.Response(200, json=[[0.1, 0.2] for _ in captura["cuerpo"]["inputs"]])

    return httpx.MockTransport(manejador)


def test_embed_texts_sync_llama_a_embed_y_devuelve_vectores() -> None:
    captura: dict[str, Any] = {}
    real = httpx.Client

    def cliente(**kw: Any) -> httpx.Client:
        return real(transport=_transporte(captura), **kw)

    with patch("kallicode_codemapping.embeddings.httpx.Client", side_effect=cliente):
        vectores = embeddings.TEIEmbeddingClient("http://tei:8080/").embed_texts_sync(["a", "b"])

    assert vectores == [[0.1, 0.2], [0.1, 0.2]]
    assert captura["url"] == "http://tei:8080/embed"
    assert captura["cuerpo"] == {"inputs": ["a", "b"], "truncate": True}


@pytest.mark.asyncio
async def test_embed_texts_async_llama_a_embed_y_devuelve_vectores() -> None:
    captura: dict[str, Any] = {}
    real = httpx.AsyncClient

    def cliente(**kw: Any) -> httpx.AsyncClient:
        return real(transport=_transporte(captura), **kw)

    with patch("kallicode_codemapping.embeddings.httpx.AsyncClient", side_effect=cliente):
        vectores = await embeddings.TEIEmbeddingClient("http://tei:8080").embed_texts(["x"])

    assert vectores == [[0.1, 0.2]]
    assert captura["cuerpo"]["truncate"] is True


@pytest.mark.asyncio
async def test_embed_texts_sin_textos_no_hace_peticion() -> None:
    cliente = embeddings.TEIEmbeddingClient("http://tei:8080")
    assert await cliente.embed_texts([]) == []
    assert cliente.embed_texts_sync([]) == []


def test_embed_texts_sync_propaga_errores_http() -> None:
    real = httpx.Client

    def cliente(**kw: Any) -> httpx.Client:
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw)

    with (
        patch("kallicode_codemapping.embeddings.httpx.Client", side_effect=cliente),
        pytest.raises(httpx.HTTPStatusError),
    ):
        embeddings.TEIEmbeddingClient("http://tei:8080").embed_texts_sync(["a"])


def test_embed_and_upsert_estructura_items_con_vector_y_hash() -> None:
    cfg = build_settings(tenant_id="t", repo_url="x")
    items = [{"node_ref": "n1", "text": "uno"}, {"id": "n2", "text": "dos", "payload": {"a": 1}}]
    with patch("kallicode_codemapping.embeddings.TEIEmbeddingClient") as tei:
        tei.return_value.embed_texts_sync.return_value = [[1.0], [2.0]]
        res = embeddings.embed_and_upsert(cfg, "definitions", items)

    assert [r["vector"] for r in res] == [[1.0], [2.0]]
    assert res[1]["payload"] == {"a": 1}
    assert res[0]["contenido_hash"] == embeddings.calcular_contenido_hash("uno")
    assert res[0]["tenant_id"] == "t"


def test_embed_and_upsert_sin_items_devuelve_lista_vacia() -> None:
    cfg = build_settings(tenant_id="t", repo_url="x")
    assert embeddings.embed_and_upsert(cfg, "code", []) == []


# --------------------------------------------------------------------------
# cm_db
# --------------------------------------------------------------------------


def _sesion_doble() -> MagicMock:
    db = MagicMock(name="Session")
    db.execute.return_value.mappings.return_value.first.return_value = {"a": 1}
    db.execute.return_value.mappings.return_value.all.return_value = [{"a": 1}, {"a": 2}]
    return db


def test_uno_devuelve_dict_o_none() -> None:
    db = _sesion_doble()
    assert cm_db.uno(db, "SELECT 1") == {"a": 1}
    db.execute.return_value.mappings.return_value.first.return_value = None
    assert cm_db.uno(db, "SELECT 1", {"x": 1}) is None


def test_todos_devuelve_lista_de_dicts() -> None:
    assert cm_db.todos(_sesion_doble(), "SELECT 1") == [{"a": 1}, {"a": 2}]


def test_ejecutar_pasa_parametros_o_diccionario_vacio() -> None:
    db = _sesion_doble()
    cm_db.ejecutar(db, "UPDATE x SET a = 1")
    assert db.execute.call_args.args[1] == {}
    cm_db.ejecutar(db, "UPDATE x SET a = :a", {"a": 2})
    assert db.execute.call_args.args[1] == {"a": 2}


def test_ejecutar_lote_ignora_listas_vacias() -> None:
    db = _sesion_doble()
    cm_db.ejecutar_lote(db, "INSERT INTO x VALUES (:a)", [])
    db.execute.assert_not_called()
    cm_db.ejecutar_lote(db, "INSERT INTO x VALUES (:a)", [{"a": 1}, {"a": 2}])
    assert db.execute.call_args.args[1] == [{"a": 1}, {"a": 2}]


def test_engine_se_reutiliza_por_url() -> None:
    cm_db._engines.clear()
    with patch(
        "kallicode_codemapping.cm_db.create_engine", side_effect=lambda *a, **k: object()
    ) as crear:
        a = cm_db._engine_for("postgresql+psycopg://u:p@h/a")
        b = cm_db._engine_for("postgresql+psycopg://u:p@h/a")
        c = cm_db._engine_for("postgresql+psycopg://u:p@h/b")
    assert a is b
    assert a is not c
    assert crear.call_count == 2
    cm_db._engines.clear()


def test_sesion_tenant_fija_el_tenant_antes_de_ceder_la_sesion() -> None:
    db = _sesion_doble()
    maker = MagicMock()
    maker.return_value.__enter__.return_value = db
    maker.return_value.__exit__.return_value = False
    db.begin.return_value.__enter__.return_value = None
    db.begin.return_value.__exit__.return_value = False

    with (
        patch("kallicode_codemapping.cm_db._engine_for"),
        patch("kallicode_codemapping.cm_db.sessionmaker", return_value=maker),
        cm_db.sesion_tenant("postgresql+psycopg://u:p@h/db", "org_a") as sesion,
    ):
        assert sesion is db
        sql, params = db.execute.call_args.args
        assert "app.tenant_id" in str(sql)
        assert params == {"t": "org_a"}


# --------------------------------------------------------------------------
# extract_sql
# --------------------------------------------------------------------------


def test_build_client_engine_exige_database_url() -> None:
    cfg = build_settings(tenant_id="t", repo_url="x")
    with pytest.raises(ValueError, match="database_url"):
        extract_sql.build_client_engine(cfg)


def test_build_client_engine_aplica_salvaguardas() -> None:
    cfg = build_settings(
        tenant_id="t",
        repo_url="x",
        database_url="postgresql+psycopg://u:p@cliente.example/db",
        client_db_max_connections=3,
        client_db_statement_timeout_ms=1500,
    )
    with patch("kallicode_codemapping.extract_sql.create_engine") as crear:
        extract_sql.build_client_engine(cfg)

    kwargs = crear.call_args.kwargs
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 0
    opciones = kwargs["connect_args"]["options"]
    assert "statement_timeout=1500" in opciones
    assert "default_transaction_read_only=on" in opciones


def _inspector() -> MagicMock:
    insp = MagicMock()
    insp.get_schema_names.return_value = ["public", "pg_catalog"]
    insp.get_table_names.return_value = ["pedidos"]
    insp.get_columns.return_value = [{"name": "id", "type": "INTEGER", "nullable": False}]
    insp.get_foreign_keys.return_value = [
        {
            "constrained_columns": ["cliente_id"],
            "referred_table": "clientes",
            "referred_schema": None,
            "referred_columns": ["id"],
        }
    ]
    return insp


def test_read_schemas_omite_esquemas_excluidos_y_normaliza_fks() -> None:
    tablas, fks = extract_sql._read_schemas(_inspector(), {"pg_catalog"})

    assert tablas == [
        {
            "name": "pedidos",
            "esquema": "public",
            "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
        }
    ]
    assert fks == [
        {
            "from_table": "pedidos",
            "from_schema": "public",
            "from_cols": ["cliente_id"],
            "to_table": "clientes",
            "to_schema": "public",
            "to_cols": ["id"],
        }
    ]


def _engine_doble(error: Exception | None = None) -> MagicMock:
    engine = MagicMock()

    @contextmanager
    def conectar() -> Any:
        if error is not None:
            raise error
        yield MagicMock()

    engine.connect.side_effect = conectar
    return engine


def _cfg_cliente() -> Any:
    return build_settings(
        tenant_id="t", repo_url="x", database_url="postgresql+psycopg://u:p@c.example/db"
    )


def test_extract_schema_devuelve_tablas_y_libera_el_engine() -> None:
    engine = _engine_doble()
    with (
        patch.object(extract_sql, "build_client_engine", return_value=engine),
        patch.object(extract_sql, "inspect", return_value=_inspector()),
    ):
        tablas, fks = extract_sql.extract_schema(_cfg_cliente())
    assert [t["name"] for t in tablas] == ["pedidos"]
    assert fks[0]["to_table"] == "clientes"
    engine.dispose.assert_called_once()


def _error_db(sqlstate: str | None) -> Any:
    from sqlalchemy.exc import DBAPIError

    orig = MagicMock(spec=[])
    if sqlstate is not None:
        orig.sqlstate = sqlstate
    return DBAPIError("SELECT 1", {}, orig)


def test_extract_schema_traduce_cancelacion_por_timeout() -> None:
    engine = _engine_doble(_error_db("57014"))
    with (
        patch.object(extract_sql, "build_client_engine", return_value=engine),
        pytest.raises(extract_sql.SchemaExtractionTimeout),
    ):
        extract_sql.extract_schema(_cfg_cliente())
    engine.dispose.assert_called_once()


def test_extract_schema_propaga_otros_errores_de_base() -> None:
    engine = _engine_doble(_error_db(None))
    with (
        patch.object(extract_sql, "build_client_engine", return_value=engine),
        pytest.raises(Exception) as exc,
    ):
        extract_sql.extract_schema(_cfg_cliente())
    assert not isinstance(exc.value, extract_sql.SchemaExtractionTimeout)
    engine.dispose.assert_called_once()


def test_find_table_usage_detecta_solo_tablas_conocidas() -> None:
    archivos = [
        SourceFile(
            path="repo.py",
            language="python",
            sha256="h",
            content='db.execute("SELECT * FROM Pedidos JOIN clientes ON 1=1")\nx = "update otra SET a=1"',
        ),
        SourceFile(path="vacio.py", language="python", sha256="h", content="x = 1"),
    ]
    conocidas = [{"name": "pedidos"}, {"name": "clientes"}]

    uso = extract_sql.find_table_usage(archivos, conocidas)

    # KC-CM-09-3: cada arista lleva el esquema resuelto (public si la tabla no lo declara).
    assert uso == [
        {"file": "repo.py", "esquema": "public", "table": "clientes"},
        {"file": "repo.py", "esquema": "public", "table": "pedidos"},
    ]
