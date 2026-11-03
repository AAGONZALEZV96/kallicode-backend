"""Pruebas unitarias dirigidas para alcanzar > 90% de mutación en CodeMapping.

Cubre exhaustivamente:
- budget.py
- checkpoints.py
- embeddings.py
- tenant_thresholds.py
"""

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
def test_mutantes_tenant_thresholds_constantes_y_validaciones() -> None:
    assert UMBRAL_CONFIANZA_DEFAULT == 0.75

    # Overrides válidos exactos
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.0) == 0.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0) == 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.5) == 0.5

    # Fronteras inválidas (mata mutantes de < 0.0 y > 1.0)
    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=-0.0001)

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0001)


def test_mutantes_tenant_thresholds_ramas_db() -> None:
    # Conexión None o cerrada
    assert obtener_umbral_confianza_tenant(None, "t1") == 0.75
    conn_closed = MagicMock()
    conn_closed.closed = True
    assert obtener_umbral_confianza_tenant(conn_closed, "t1") == 0.75

    # Excepción al consultar cursor
    conn_err = MagicMock()
    conn_err.closed = False
    conn_err.cursor.side_effect = RuntimeError("DB caida")
    assert obtener_umbral_confianza_tenant(conn_err, "t1") == 0.75

    # Simulación de cursor
    conn = MagicMock()
    conn.closed = False
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    # fetchone devuelve None
    cur.fetchone.return_value = None
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75

    # fetchone devuelve fila con None
    cur.fetchone.return_value = (None,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.75

    # Clamping de valores menores a 0.0 y mayores a 1.0
    cur.fetchone.return_value = (-0.1,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.0

    cur.fetchone.return_value = (1.1,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 1.0

    cur.fetchone.return_value = (0.0,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.0

    cur.fetchone.return_value = (1.0,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 1.0

    cur.fetchone.return_value = (0.83,)
    assert obtener_umbral_confianza_tenant(conn, "t1") == 0.83

    # Verificación de parámetros exactos (default, tenant_id)
    assert cur.execute.call_args[0][1] == (0.75, "t1")


# =====================================================================
# 2. MUTANTES EN budget.py
# =====================================================================
def test_mutantes_budget_constantes_y_calculos() -> None:
    # estimar_tokens_texto
    assert estimar_tokens_texto("") == 0
    assert estimar_tokens_texto("a") == 1
    assert estimar_tokens_texto("123") == 1
    assert estimar_tokens_texto("1234") == 2
    assert estimar_tokens_texto("1234567") == 2
    assert estimar_tokens_texto("12345678") == 3

    # estimar_tokens_ingesta vacío
    assert estimar_tokens_ingesta([]) == (0, 0)

    # Símbolo con campos vacíos o None
    simbolo_vacio = {"signature": "", "snippet": ""}
    p_v, c_v = estimar_tokens_ingesta([simbolo_vacio])
    assert p_v > 0
    assert c_v == 150

    simbolo_none = {"signature": None, "snippet": None}
    p_n, c_n = estimar_tokens_ingesta([simbolo_none])
    assert p_n > 0
    assert c_n == 150

    # Símbolo con recorte exacto en 1000 caracteres
    s_1000 = {"signature": "1234567", "snippet": "a" * 1000}
    s_2000 = {"signature": "1234567", "snippet": "a" * 2000}
    assert estimar_tokens_ingesta([s_1000]) == estimar_tokens_ingesta([s_2000])
    p_1000, c_1000 = estimar_tokens_ingesta([s_1000])
    assert p_1000 > 0
    assert c_1000 == 150


def test_mutantes_budget_verificar_presupuesto_completo() -> None:
    simbolos = [{"signature": "def fn():", "snippet": "return 1"}]
    p_est, c_est = estimar_tokens_ingesta(simbolos)
    tot_req = p_est + c_est

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    # 1. Tenant sin suscripción (row is None -> límite default 500_000, consumido 0)
    cur.fetchone.return_value = None
    res_def = verificar_presupuesto_tenant(conn, "t_default", simbolos, precio_por_millon_usd=1.0)
    assert isinstance(res_def, EstimacionPresupuesto)
    assert res_def.cuota_disponible == 500_000

    # 2. Suscripción explícita con saldo suficiente
    cur.fetchone.return_value = (20_000, 5_000)
    res_ok = verificar_presupuesto_tenant(conn, "t_ok", simbolos)
    assert res_ok.cuota_disponible == 15_000

    # 3. Consumido excede la cuota contratada
    cur.fetchone.return_value = (5_000, 6_000)
    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn, "t_excedido", simbolos)
    mensaje = str(exc_info.value)
    assert "t_excedido" in mensaje
    assert str(tot_req) in mensaje

    # 4. Caso de frontera exacto: disponibles == requeridos (NO debe lanzar excepción)
    cur.fetchone.return_value = (tot_req + 500, 500)
    res_limite = verificar_presupuesto_tenant(conn, "t_limite", simbolos)
    assert res_limite.cuota_disponible == tot_req

    # 5. Caso de frontera: disponibles == requeridos - 1 (SÍ debe lanzar excepción)
    cur.fetchone.return_value = (tot_req + 499, 500)
    with pytest.raises(BudgetExceededError):
        verificar_presupuesto_tenant(conn, "t_limite_menos_uno", simbolos)


# =====================================================================
# 3. MUTANTES EN checkpoints.py
# =====================================================================
def test_mutantes_checkpoints_rutas_y_persistencia(tmp_path: Path) -> None:
    workdir = str(tmp_path)
    tenant_id = "tenant_chk"
    snapshot_id = "snap_789"

    dir_path = _directorio(workdir, tenant_id, snapshot_id)
    assert isinstance(dir_path, Path)
    assert tenant_id in str(dir_path)
    assert snapshot_id in str(dir_path)

    # Archivo inexistente retorna None
    assert cargar(workdir, tenant_id, snapshot_id, "fase_inexistente") is None

    # Guardar fase y verificar contenido
    datos = {"contador": 10, "completado": True, "lista": [1, 2, 3]}
    guardar(workdir, tenant_id, snapshot_id, "fase_a", datos)

    # Cargar fase existente y verificar integridad
    cargado = cargar(workdir, tenant_id, snapshot_id, "fase_a")
    assert cargado is not None
    assert cargado == datos
    assert cargado["contador"] == 10

    # Limpiar directorio de snapshot
    limpiar(workdir, tenant_id, snapshot_id)
    assert not dir_path.exists()
    assert cargar(workdir, tenant_id, snapshot_id, "fase_a") is None

    # Limpiar directorio inexistente no debe fallar
    limpiar(workdir, tenant_id, "snap_inexistente")


# =====================================================================
# 4. MUTANTES EN embeddings.py
# =====================================================================
def test_mutantes_embeddings_uuid5_determinista() -> None:
    u1 = generar_uuid5_vector("tenant_1", "code", "sym_a")
    u2 = generar_uuid5_vector("tenant_1", "code", "sym_a")
    u_dif_tenant = generar_uuid5_vector("tenant_2", "code", "sym_a")
    u_dif_capa = generar_uuid5_vector("tenant_1", "doc", "sym_a")
    u_dif_nodo = generar_uuid5_vector("tenant_1", "code", "sym_b")

    assert isinstance(u1, uuid.UUID)
    assert u1 == u2
    assert u1 != u_dif_tenant
    assert u1 != u_dif_capa
    assert u1 != u_dif_nodo


def test_mutantes_embeddings_cliente_tei_llamadas_y_reintentos() -> None:
    client = TEIEmbeddingClient(base_url="http://tei.local:8080/")
    assert client.base_url == "http://tei.local:8080"

    # Lista vacía retorna directo sin invocar httpx
    assert client.embed_texts_sync([]) == []

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [[0.1, 0.2], [0.3, 0.4]]

    # 1. Llamada exitosa directa
    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.return_value = mock_resp

        vecs = client.embed_texts_sync(["hola", "mundo"])
        assert vecs == [[0.1, 0.2], [0.3, 0.4]]
        assert mock_http.post.call_count == 1

    # 2. Fallo de conexión lanza excepción
    with patch("httpx.Client") as mock_client_cls, patch("time.sleep"):
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http
        mock_http.post.side_effect = httpx.ConnectError("error definitivo")

        with pytest.raises(httpx.ConnectError):
            client.embed_texts_sync(["falla"])
