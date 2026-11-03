"""Pruebas unitarias dirigidas para matar mutantes en los 4 módulos de mutmut (> 95%)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    EstimacionPresupuesto,
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
    generar_uuid5_vector,
)
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)


# =====================================================================
# 1. MUTANTES EN tenant_thresholds.py
# =====================================================================
def test_mutantes_tenant_thresholds_exhaustivo() -> None:
    assert UMBRAL_CONFIANZA_DEFAULT == 0.75

    # Overrides en límites exactos 0.0 y 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.0) == 0.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0) == 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.42) == 0.42

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=-0.01)

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.01)

    # Conexión None o cerrada
    assert obtener_umbral_confianza_tenant(None, "t1") == 0.75
    conn_closed = MagicMock()
    conn_closed.closed = True
    assert obtener_umbral_confianza_tenant(conn_closed, "t1") == 0.75

    # Excepción en cursor
    conn_err = MagicMock()
    conn_err.closed = False
    conn_err.cursor.side_effect = RuntimeError("fallo db")
    assert obtener_umbral_confianza_tenant(conn_err, "t1") == 0.75

    # Valores desde base de datos
    conn = MagicMock()
    conn.closed = False
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    cur.fetchone.return_value = None
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75

    cur.fetchone.return_value = (None,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75

    # Clamping en límites inferior (< 0.0) y superior (> 1.0)
    cur.fetchone.return_value = (-0.5,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.0

    cur.fetchone.return_value = (1.5,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 1.0

    cur.fetchone.return_value = (0.0,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.0

    cur.fetchone.return_value = (1.0,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 1.0

    cur.fetchone.return_value = ("0.85",)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.85

    # Parámetros reales enviados a execute: (default_umbral, tenant_id)
    assert cur.execute.call_args[0][1] == (0.75, "t1")


# =====================================================================
# 2. MUTANTES EN budget.py
# =====================================================================
def test_mutantes_budget_constantes_y_calculos() -> None:
    # estimar_tokens_texto
    assert estimar_tokens_texto("") == 0
    assert estimar_tokens_texto("123") == 1
    assert estimar_tokens_texto("1234") == 2
    assert estimar_tokens_texto("1234567") == 2
    assert estimar_tokens_texto("12345678") == 3

    # estimar_tokens_ingesta vacío
    assert estimar_tokens_ingesta([]) == (0, 0)

    # Símbolo con valores nulos
    p_nulo, c_nulo = estimar_tokens_ingesta([{"signature": None, "snippet": None}])
    assert p_nulo > 0
    assert c_nulo == 150

    # Símbolo con snippet que excede 1000 caracteres (verifica corte [:1000])
    s_largo = {"signature": "1234567", "snippet": "a" * 2000}
    s_1000 = {"signature": "1234567", "snippet": "a" * 1000}
    assert estimar_tokens_ingesta([s_largo]) == estimar_tokens_ingesta([s_1000])
    p_1000, c_1000 = estimar_tokens_ingesta([s_1000])
    assert p_1000 > 0
    assert c_1000 == 150


def test_mutantes_budget_verificar_presupuesto_completo() -> None:
    simbolos = [{"signature": "def a():", "snippet": "pass"}]
    p_est, c_est = estimar_tokens_ingesta(simbolos)
    tot_req = p_est + c_est

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    # 1. Row is None -> cuota_maxima = 500_000, consumido = 0
    cur.fetchone.return_value = None
    res = verificar_presupuesto_tenant(conn, "tenant-1", simbolos, precio_por_millon_usd=2.0)
    assert isinstance(res, EstimacionPresupuesto)
    assert res.cuota_disponible == 500_000

    # 2. Row con datos -> cuota_disponible = max(0, cuota_maxima - consumido)
    cur.fetchone.return_value = (10_000, 4_000)
    res2 = verificar_presupuesto_tenant(conn, "tenant-2", simbolos)
    assert res2.cuota_disponible == 6_000

    # 3. Consumido mayor que cuota_maxima -> disponible = 0 -> lanza BudgetExceededError
    cur.fetchone.return_value = (1_000, 1_500)
    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn, "tenant-3", simbolos)
    mensaje = str(exc_info.value)
    assert "tenant-3" in mensaje
    assert str(tot_req) in mensaje

    # 4. Límite exacto: requeridos == disponibles
    cur.fetchone.return_value = (tot_req + 100, 100)
    res_exacto = verificar_presupuesto_tenant(conn, "tenant-exacto", simbolos)
    assert res_exacto.cuota_disponible == tot_req

    # 5. Un token por encima del disponible
    cur.fetchone.return_value = (tot_req + 99, 100)
    with pytest.raises(BudgetExceededError):
        verificar_presupuesto_tenant(conn, "tenant-uno-menos", simbolos)


# =====================================================================
# 3. MUTANTES EN checkpoints.py
# =====================================================================
def test_mutantes_checkpoints_ciclo_completo(tmp_path: Path) -> None:
    workdir = str(tmp_path)
    tenant_id = "tenant_test"
    snapshot_id = "snap_456"

    # Verificar generación del directorio
    dir_path = _directorio(workdir, tenant_id, snapshot_id)
    assert isinstance(dir_path, Path)
    assert tenant_id in str(dir_path)
    assert snapshot_id in str(dir_path)

    # Cargar cuando no existe retorna None
    assert cargar(workdir, tenant_id, snapshot_id, "fase_inexistente") is None

    # Guardar un payload válido
    payload = {"status": "ok", "archivos": 42, "items": ["a", "b"]}
    guardar(workdir, tenant_id, snapshot_id, "fase1", payload)

    # Cargar el payload guardado y verificar integridad
    cargado = cargar(workdir, tenant_id, snapshot_id, "fase1")
    assert cargado is not None
    assert cargado == payload
    assert cargado["archivos"] == 42
    assert cargado["items"] == ["a", "b"]

    # Limpiar elimina el directorio del snapshot
    limpiar(workdir, tenant_id, snapshot_id)
    assert not dir_path.exists()
    assert cargar(workdir, tenant_id, snapshot_id, "fase1") is None

    # Limpiar un directorio que no existe no lanza excepción
    limpiar(workdir, tenant_id, "snap_inexistente")


# =====================================================================
# 4. MUTANTES EN embeddings.py
# =====================================================================
def test_mutantes_embeddings_uuid_y_tei_client() -> None:
    u1 = generar_uuid5_vector("tenant_a", "code", "sym_1")
    u2 = generar_uuid5_vector("tenant_a", "code", "sym_1")
    u_diff_tenant = generar_uuid5_vector("tenant_b", "code", "sym_1")
    u_diff_layer = generar_uuid5_vector("tenant_a", "domain", "sym_1")
    u_diff_ref = generar_uuid5_vector("tenant_a", "code", "sym_2")

    assert isinstance(u1, uuid.UUID)
    assert u1 == u2
    assert u1 != u_diff_tenant
    assert u1 != u_diff_layer
    assert u1 != u_diff_ref

    client = TEIEmbeddingClient(base_url="http://tei.local:8080")
    assert client.base_url == "http://tei.local:8080"
    assert client.embed_texts_sync([]) == []

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [[0.1, 0.2], [0.3, 0.4]]

    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.return_value = mock_resp

        vecs = client.embed_texts_sync(["t1", "t2"])
        assert vecs == [[0.1, 0.2], [0.3, 0.4]]
        assert mock_http.post.call_count == 1


def test_mutantes_embeddings_reintentos_y_fallo() -> None:
    client = TEIEmbeddingClient(base_url="http://tei.local:8080")

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = [[1.0, 2.0]]

    # 1. Éxito normal con llamada HTTP
    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.return_value = mock_resp_ok

        res = client.embed_texts_sync(["hola"])
        assert res == [[1.0, 2.0]]

    # 2. Falla de conexión repetida que agota reintentos y lanza excepción
    with patch("httpx.Client") as mock_client_cls, patch("time.sleep"):
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.side_effect = httpx.ConnectError("servidor caido")

        with pytest.raises(httpx.ConnectError):
            client.embed_texts_sync(["hola"])
