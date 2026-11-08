"""Pruebas unitarias dirigidas para alcanzar > 90% de mutación en CodeMapping.

Cubre exhaustivamente:
- budget.py
- checkpoints.py
- embeddings.py (incluyendo preparar_items_doc_chunks y embed_and_upsert)
- tenant_thresholds.py
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    estimar_tokens_texto,
    verificar_presupuesto_tenant,
)
from kallicode_codemapping.checkpoints import (
    _directorio,
    cargar,
    guardar,
    limpiar,
)
from kallicode_codemapping.embeddings import (
    TEIEmbeddingClient,
    calcular_contenido_hash,
    embed_and_upsert,
    generar_uuid5_vector,
    preparar_items_doc_chunks,
    preparar_items_vectoriales,
)
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)


# =====================================================================
# 1. TENANT THRESHOLDS
# =====================================================================
def test_mutantes_tenant_thresholds_exhaustivo() -> None:
    assert UMBRAL_CONFIANZA_DEFAULT == 0.75
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.0) == 0.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0) == 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.5) == 0.5

    with pytest.raises(ValueError, match="rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=-0.0001)
    with pytest.raises(ValueError, match="rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0001)

    assert obtener_umbral_confianza_tenant(None, "t1") == 0.75
    conn_closed = MagicMock()
    conn_closed.closed = True
    assert obtener_umbral_confianza_tenant(conn_closed, "t1") == 0.75

    conn_err = MagicMock()
    conn_err.closed = False
    conn_err.cursor.side_effect = RuntimeError("DB caida")
    assert obtener_umbral_confianza_tenant(conn_err, "t1") == 0.75

    conn = MagicMock()
    conn.closed = False
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    cur.fetchone.return_value = None
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75
    cur.fetchone.return_value = (None,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75

    cur.fetchone.return_value = (-0.1,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.0
    cur.fetchone.return_value = (1.1,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 1.0
    cur.fetchone.return_value = (0.83,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.83

    assert cur.execute.call_args[0][1] == (0.75, "t1")


# =====================================================================
# 2. BUDGET
# =====================================================================
def test_mutantes_budget_calculos_y_constantes() -> None:
    assert estimar_tokens_texto("") == 0
    assert estimar_tokens_texto("a") == 1
    assert estimar_tokens_texto("12345678") == 3
    assert estimar_tokens_ingesta([]) == (0, 0)

    p_v, c_v = estimar_tokens_ingesta([{"signature": "", "snippet": ""}])
    assert p_v == 138
    assert c_v == 150

    p_n, _c_n = estimar_tokens_ingesta([{"signature": None, "snippet": None}])
    assert p_n == 138

    s_1000 = {"signature": "sig", "snippet": "a" * 1000}
    s_2000 = {"signature": "sig", "snippet": "a" * 2000}
    assert estimar_tokens_ingesta([s_1000]) == estimar_tokens_ingesta([s_2000])


def test_mutantes_budget_verificar_presupuesto_completo() -> None:
    simbolos = [{"signature": "def fn():", "snippet": "return 1"}]
    p_est, c_est = estimar_tokens_ingesta(simbolos)
    tot_req = p_est + c_est

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    cur.fetchone.return_value = None
    res_def = verificar_presupuesto_tenant(conn, "t_def", simbolos, precio_por_millon_usd=1.0)
    assert res_def.cuota_disponible == 500_000

    cur.fetchone.return_value = (20_000, 5_000)
    res_ok = verificar_presupuesto_tenant(conn, "t_ok", simbolos)
    assert res_ok.cuota_disponible == 15_000

    cur.fetchone.return_value = (5_000, 6_000)
    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn, "t_excedido", simbolos)
    assert str(tot_req) in str(exc_info.value)

    cur.fetchone.return_value = (tot_req + 500, 500)
    assert verificar_presupuesto_tenant(conn, "t_limite", simbolos).cuota_disponible == tot_req

    cur.fetchone.return_value = (tot_req + 499, 500)
    with pytest.raises(BudgetExceededError):
        verificar_presupuesto_tenant(conn, "t_limite_menos_uno", simbolos)


# =====================================================================
# 3. CHECKPOINTS
# =====================================================================
def test_mutantes_checkpoints_rutas_y_persistencia(tmp_path: Path) -> None:
    workdir = str(tmp_path)
    dir_path = _directorio(workdir, "tenant_chk", "snap_789")
    assert cargar(workdir, "tenant_chk", "snap_789", "no_existe") is None

    datos = {"contador": 10, "completado": True, "lista": [1, 2, 3]}
    guardar(workdir, "tenant_chk", "snap_789", "fase_a", datos)
    assert (dir_path / "fase_a.json").exists()
    assert cargar(workdir, "tenant_chk", "snap_789", "fase_a") == datos

    limpiar(workdir, "tenant_chk", "snap_789")
    assert not dir_path.exists()
    limpiar(workdir, "tenant_chk", "snap_inexistente")


# =====================================================================
# 4. EMBEDDINGS (Resolución final de 44 no tests)
# =====================================================================
def test_mutantes_embeddings_doc_chunks_aniquilar_44_no_tests() -> None:
    # 1. Creamos un mock con atributos de objeto en lugar de un diccionario
    chunk_mock = MagicMock()
    chunk_mock.documento_id = "doc_1"
    chunk_mock.chunk_idx = 0
    chunk_mock.texto = "contenido de prueba"
    chunk_mock.text = "contenido de prueba"
    chunk_mock.page_content = "contenido de prueba"
    chunk_mock.metadata = {"seccion": "intro"}

    chunks = [chunk_mock]

    chunk_mock.seccion = "intro"
    chunk_mock.contenido_hash = "h" * 64
    chunk_mock.tipo_doc = "manual"
    vectores = [[0.5] * 4]

    items = preparar_items_doc_chunks(chunks, vectores, tenant_id="tenant_doc")

    assert len(items) == 1
    item = items[0]
    assert item["tenant_id"] == "tenant_doc"
    assert item["id"] == str(generar_uuid5_vector("tenant_doc", "docs", "doc_1#0"))
    assert item["documento_id"] == "doc_1"
    assert item["chunk_idx"] == 0
    assert item["vector"] == [0.5] * 4
    assert item["contenido_hash"] == "h" * 64
    assert item["modelo_embedding"] == "bge-m3"
    assert item["metadata"] == {"seccion": "intro"}

    # 3. Probamos el caso vacío
    assert preparar_items_doc_chunks([], [], tenant_id="tenant_doc") == []


def test_mutantes_embeddings_vectoriales_y_hashes() -> None:
    assert isinstance(generar_uuid5_vector("tenant_1", "code", "sym_a"), uuid.UUID)
    assert len(calcular_contenido_hash("texto")) == 64

    items = [{"node_ref": "sym_x", "text": "def test(): pass", "payload": {"meta": 1}}]
    preparados = preparar_items_vectoriales("tenant_1", "code", items)
    assert len(preparados) == 1
    assert preparados[0]["payload"] == {"meta": 1}


def test_mutantes_embeddings_cliente_tei_y_upsert() -> None:
    client = TEIEmbeddingClient(base_url="http://tei.local:8080/")
    assert client.embed_texts_sync([]) == []

    # 1. Probamos el cliente interceptando la red a bajo nivel
    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.return_value.status_code = 200
        mock_http.post.return_value.json.return_value = [[0.1, 0.2]]
        assert client.embed_texts_sync(["hola"]) == [[0.1, 0.2]]

    # 2. embed_and_upsert(settings, layer, items) -> list[dict]
    settings = MagicMock()
    settings.tei_endpoint_url = "http://tei.local:8080"
    settings.tenant_id = "tenant_1"
    items_a_insertar = [{"node_ref": "sym_a", "text": "texto 1", "payload": {"k": 1}}]

    with patch(
        "kallicode_codemapping.embeddings.TEIEmbeddingClient.embed_texts_sync"
    ) as mock_embed:
        mock_embed.return_value = [[0.1, 0.2]]
        resultado = embed_and_upsert(settings, "code", items_a_insertar)

    mock_embed.assert_called_once_with(["texto 1"])
    assert len(resultado) == 1
    fila = resultado[0]
    assert fila["id"] == str(generar_uuid5_vector("tenant_1", "code", "sym_a"))
    assert fila["tenant_id"] == "tenant_1"
    assert fila["layer"] == "code"
    assert fila["node_ref"] == "sym_a"
    assert fila["vector"] == [0.1, 0.2]
    assert fila["contenido_hash"] == calcular_contenido_hash("texto 1")
    assert fila["payload"] == {"k": 1}

    assert embed_and_upsert(settings, "code", []) == []
