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
    DIMENSIONES_BGE_M3,
    MODELO_EMBEDDING,
    NAMESPACE_KALLICODE,
    TAMANO_LOTE_TEI,
    EmbeddingInvalidoError,
    TEIEmbeddingClient,
    calcular_contenido_hash,
    embeber_en_lotes,
    embed_and_upsert,
    generar_uuid5_vector,
    id_doc_chunk,
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


# =====================================================================
# 5. EMBEDDINGS DE DOCUMENTACIÓN (KC-CM-08-1)
# =====================================================================
def test_mutantes_embeddings_constantes_doc_chunks() -> None:
    assert DIMENSIONES_BGE_M3 == 1024
    assert MODELO_EMBEDDING == "bge-m3"
    assert TAMANO_LOTE_TEI == 32
    assert issubclass(EmbeddingInvalidoError, ValueError)


def test_mutantes_id_doc_chunk_canonico() -> None:
    esperado = str(uuid.uuid5(NAMESPACE_KALLICODE, "t1:docs:doc_9#3"))
    assert id_doc_chunk("t1", "doc_9", 3) == esperado
    assert id_doc_chunk("t1", "doc_9", 3) == str(generar_uuid5_vector("t1", "docs", "doc_9#3"))
    assert id_doc_chunk("t1", "doc_9", 4) != esperado
    assert id_doc_chunk("t2", "doc_9", 3) != esperado


def test_mutantes_embeber_en_lotes_fronteras() -> None:
    llamadas: list[list[str]] = []

    class Cliente:
        def __init__(self, dims: int = 4, faltan: int = 0) -> None:
            self.dims = dims
            self.faltan = faltan

        def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
            llamadas.append(list(texts))
            vectores = [[float(len(t))] * self.dims for t in texts]
            return vectores[: len(vectores) - self.faltan]

    textos = [f"t{i}" for i in range(5)]

    # Lotes exactos: 2 + 2 + 1, en orden, y el resultado concatenado en el mismo orden.
    vectores = embeber_en_lotes(Cliente(), textos, tamano_lote=2, dimensiones=4)  # type: ignore[arg-type]
    assert llamadas == [["t0", "t1"], ["t2", "t3"], ["t4"]]
    assert vectores == [[2.0] * 4] * 5

    # Un solo lote cuando cabe justo; por defecto 32 y 1024 dimensiones.
    llamadas.clear()
    assert len(embeber_en_lotes(Cliente(dims=1024), ["a"] * 32)) == 32  # type: ignore[arg-type]
    assert len(llamadas) == 1
    llamadas.clear()
    embeber_en_lotes(Cliente(dims=1024), ["a"] * 33)  # type: ignore[arg-type]
    assert [len(lote) for lote in llamadas] == [32, 1]

    # Sin textos no se llama a TEI.
    llamadas.clear()
    assert embeber_en_lotes(Cliente(), [], tamano_lote=1, dimensiones=4) == []  # type: ignore[arg-type]
    assert llamadas == []

    # tamano_lote = 1 es válido; 0 no.
    assert len(embeber_en_lotes(Cliente(), ["a", "b"], tamano_lote=1, dimensiones=4)) == 2  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="tamano_lote debe ser >= 1"):
        embeber_en_lotes(Cliente(), ["a"], tamano_lote=0, dimensiones=4)  # type: ignore[arg-type]

    # Cantidad distinta de vectores.
    with pytest.raises(EmbeddingInvalidoError, match="TEI devolvio 1 vectores para 2 textos"):
        embeber_en_lotes(Cliente(faltan=1), ["a", "b"], tamano_lote=2, dimensiones=4)  # type: ignore[arg-type]

    # Dimensión distinta: una de más o de menos también se rechaza.
    for dims in (3, 5):
        with pytest.raises(
            EmbeddingInvalidoError,
            match=f"vector de {dims} dimensiones; se esperaban 4",
        ):
            embeber_en_lotes(Cliente(dims=dims), ["a"], tamano_lote=2, dimensiones=4)  # type: ignore[arg-type]


def test_mutantes_preparar_items_doc_chunks_campos() -> None:
    from kallicode_codemapping.ingest_docs import DocumentChunk

    chunk = DocumentChunk(
        documento_id="doc_7",
        chunk_idx=2,
        titulo="T",
        seccion="Sec",
        texto="contenido",
        contenido_hash="f" * 64,
        tipo_doc="adr",
        metadata={"ruta": "a.md"},
    )
    (item,) = preparar_items_doc_chunks([chunk], [[0.25] * 3], tenant_id="t9")
    assert item == {
        "id": id_doc_chunk("t9", "doc_7", 2),
        "tenant_id": "t9",
        "documento_id": "doc_7",
        "chunk_idx": 2,
        "seccion": "Sec",
        "texto": "contenido",
        "vector": [0.25] * 3,
        "modelo_embedding": "bge-m3",
        "contenido_hash": "f" * 64,
        "metadata": {"ruta": "a.md"},
        "tipo_doc": "adr",
    }
    with pytest.raises(ValueError):
        preparar_items_doc_chunks([chunk], [], tenant_id="t9")


# =====================================================================
# 6. SUPERVIVIENTES DE MUTMUT (CI 82.6 % -> objetivo >= 90 %)
#    Valores exactos, mensajes completos y SQL/HTTP que los tests previos
#    solo comprobaban de forma parcial.
# =====================================================================
def test_mutantes_budget_estimacion_exacta_y_acumulada() -> None:
    import dataclasses

    from kallicode_codemapping.budget import EstimacionPresupuesto

    # Firma de 37 chars -> 10 tokens; snippet de 74 -> 20 tokens.
    s = {"signature": "a" * 37, "snippet": "b" * 74}
    # prompt = 120 + 10 + 20 = 150; overhead ceil(150 * 0.15) = 23.
    assert estimar_tokens_ingesta([s]) == (173, 150)
    # Dos símbolos acumulan (no se sobreescriben): prompt 300 + ceil(45) = 345.
    assert estimar_tokens_ingesta([s, s]) == (345, 300)
    # Solo firma o solo snippet: cada campo se lee de su clave.
    assert estimar_tokens_ingesta([{"signature": "a" * 37}]) == (150, 150)  # 130 + ceil(19.5)
    assert estimar_tokens_ingesta([{"snippet": "b" * 74}]) == (161, 150)  # 140 + ceil(21)

    est = EstimacionPresupuesto(1, 1, 0, 0.0, 1, True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        est.total_tokens_estimados = 2  # type: ignore[misc]


def _conn_con_fila(fila: object) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = fila
    return conn, cur


def test_mutantes_budget_costo_cuota_y_sql_exactos() -> None:
    simbolos = [{"signature": "def f(x):", "snippet": "z" * 997} for _ in range(1500)]
    prompt, completion = estimar_tokens_ingesta(simbolos)
    total = prompt + completion
    esperado = round(total / 1_000_000.0 * 0.50, 4)
    assert round(total / 1_000_000.0 * 0.50, 5) != esperado  # el 5.º decimal importa

    conn, cur = _conn_con_fila((10_000_000, 1_000))
    res = verificar_presupuesto_tenant(conn, "t_costo", simbolos)  # precio por defecto 0.50
    assert res.costo_usd_estimado == esperado
    assert res.total_tokens_estimados == total
    assert res.prompt_tokens_estimados == prompt
    assert res.completion_tokens_estimados == completion
    assert res.cuota_disponible == 10_000_000 - 1_000
    assert res.presupuesto_suficiente is True

    sql_ejecutado, params = cur.execute.call_args[0]
    assert sql_ejecutado.strip().startswith("SELECT")
    assert "core.usage_tokens" in sql_ejecutado
    assert "core.ciclo_inicio" in sql_ejecutado
    assert params == ("t_costo",)

    # Precio explícito: el costo escala linealmente con él.
    conn2, _ = _conn_con_fila((10_000_000, 0))
    res2 = verificar_presupuesto_tenant(conn2, "t", simbolos, precio_por_millon_usd=2.0)
    assert res2.costo_usd_estimado == round(total / 1_000_000.0 * 2.0, 4)


def test_mutantes_budget_nulos_de_la_fila() -> None:
    simbolos = [{"signature": "s", "snippet": "x"}]
    # Cuota nula -> 1_000_000 por defecto.
    conn, _ = _conn_con_fila((None, 0))
    assert verificar_presupuesto_tenant(conn, "t", simbolos).cuota_disponible == 1_000_000
    # Consumo nulo -> 0 (y la cuota se lee de row[0] aunque row[1] sea nulo).
    conn, _ = _conn_con_fila((20_000, None))
    assert verificar_presupuesto_tenant(conn, "t", simbolos).cuota_disponible == 20_000


def test_mutantes_budget_mensaje_de_presupuesto_insuficiente_completo() -> None:
    simbolos = [{"signature": "s", "snippet": "x"}]
    prompt, completion = estimar_tokens_ingesta(simbolos)
    total = prompt + completion
    costo = total / 1_000_000.0 * 0.50

    # Consumo mayor que la cuota: la cuota restante se acota en 0, nunca negativa.
    conn, _ = _conn_con_fila((1_000, 5_000))
    with pytest.raises(BudgetExceededError) as exc:
        verificar_presupuesto_tenant(conn, "t_sin_cuota", simbolos)
    assert str(exc.value) == (
        "[KC-CM-05-5] Presupuesto insuficiente para el tenant t_sin_cuota. "
        f"Requerido: {total} tokens (~${costo:.4f} USD), "
        "Disponible en ciclo: 0 tokens."
    )


def test_mutantes_checkpoints_sobrescribe_y_rechaza_no_dict(tmp_path: Path) -> None:
    workdir = str(tmp_path)
    guardar(workdir, "t", "s", "fase", {"v": 1})
    guardar(workdir, "t", "s", "fase", {"v": 2})  # el directorio ya existe: no falla
    assert cargar(workdir, "t", "s", "fase") == {"v": 2}

    (_directorio(workdir, "t", "s") / "lista.json").write_text("[1, 2]", encoding="utf-8")
    assert cargar(workdir, "t", "s", "lista") is None
    assert _directorio(workdir, "t", "s") == Path(workdir) / "t" / "checkpoints" / "s"


def test_mutantes_embeddings_constantes_y_cliente_exactos() -> None:
    import hashlib

    from kallicode_codemapping.embeddings import LAYERS

    assert LAYERS == ("code", "definitions", "docs", "incidents")
    assert generar_uuid5_vector("t", "l", "n") == uuid.uuid5(NAMESPACE_KALLICODE, "t:l:n")
    assert calcular_contenido_hash("texto") == hashlib.sha256(b"texto").hexdigest()

    cliente = TEIEmbeddingClient(base_url="http://HOSTX/")
    assert cliente.base_url == "http://HOSTX"
    assert cliente.timeout == 30.0
    assert TEIEmbeddingClient("http://h", timeout=5.0).timeout == 5.0


def test_mutantes_embeddings_peticion_http_sync_exacta() -> None:
    cliente = TEIEmbeddingClient(base_url="http://tei.local:8080/")
    with patch("httpx.Client") as cls:
        http = cls.return_value.__enter__.return_value
        http.post.return_value.json.return_value = [[0.5]]
        assert cliente.embed_texts_sync(["hola"]) == [[0.5]]

    cls.assert_called_once_with(timeout=30.0)
    http.post.assert_called_once_with(
        "http://tei.local:8080/embed", json={"inputs": ["hola"], "truncate": True}
    )
    http.post.return_value.raise_for_status.assert_called_once_with()


async def test_mutantes_embeddings_peticion_http_async_exacta() -> None:
    from unittest.mock import AsyncMock

    cliente = TEIEmbeddingClient(base_url="http://tei.local:8080/", timeout=7.0)
    assert await cliente.embed_texts([]) == []

    with patch("httpx.AsyncClient") as cls:
        http = AsyncMock()
        cls.return_value.__aenter__.return_value = http
        respuesta = MagicMock()
        respuesta.json.return_value = [[0.1, 0.2]]
        http.post.return_value = respuesta
        assert await cliente.embed_texts(["a"]) == [[0.1, 0.2]]

    cls.assert_called_once_with(timeout=7.0)
    http.post.assert_awaited_once_with(
        "http://tei.local:8080/embed", json={"inputs": ["a"], "truncate": True}
    )
    respuesta.raise_for_status.assert_called_once_with()


def test_mutantes_embeddings_mensajes_exactos_de_lotes() -> None:
    class Cliente:
        def __init__(self, dims: int, faltan: int = 0) -> None:
            self.dims, self.faltan = dims, faltan

        def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
            v = [[0.0] * self.dims for _ in texts]
            return v[: len(v) - self.faltan]

    with pytest.raises(ValueError, match=r"^tamano_lote debe ser >= 1$"):
        embeber_en_lotes(Cliente(4), ["a"], tamano_lote=0)  # type: ignore[arg-type]
    with pytest.raises(EmbeddingInvalidoError, match=r"^TEI devolvio 1 vectores para 2 textos$"):
        embeber_en_lotes(Cliente(4, faltan=1), ["a", "b"], dimensiones=4)  # type: ignore[arg-type]
    with pytest.raises(
        EmbeddingInvalidoError,
        match=r"^TEI devolvio un vector de 3 dimensiones; se esperaban 4$",
    ):
        embeber_en_lotes(Cliente(3), ["a"], dimensiones=4)  # type: ignore[arg-type]


def test_mutantes_embeddings_items_vectoriales_completos() -> None:
    (item,) = preparar_items_vectoriales("t1", "code", [{"id": "sym_9", "text": "def g(): ..."}])
    assert item == {
        "id": str(generar_uuid5_vector("t1", "code", "sym_9")),
        "tenant_id": "t1",
        "layer": "code",
        "node_ref": "sym_9",
        "text": "def g(): ...",
        "contenido_hash": calcular_contenido_hash("def g(): ..."),
        "payload": {},
    }


def test_mutantes_embed_and_upsert_completo_y_estricto() -> None:
    settings = MagicMock()
    settings.tei_endpoint_url = "http://tei"
    settings.tenant_id = "t2"
    items = [{"id": "sym_1", "text": "uno"}]  # sin node_ref: se usa el id

    with patch.object(TEIEmbeddingClient, "embed_texts_sync", return_value=[[1.0]]):
        (fila,) = embed_and_upsert(settings, "code", items)
    assert fila == {
        "id": str(generar_uuid5_vector("t2", "code", "sym_1")),
        "tenant_id": "t2",
        "layer": "code",
        "node_ref": "sym_1",
        "text": "uno",
        "vector": [1.0],
        "contenido_hash": calcular_contenido_hash("uno"),
        "payload": {},
    }

    # Menos vectores que items: zip estricto lo detecta en vez de truncar en silencio.
    with (
        patch.object(TEIEmbeddingClient, "embed_texts_sync", return_value=[[1.0]]),
        pytest.raises(ValueError),
    ):
        embed_and_upsert(settings, "code", [*items, {"id": "sym_2", "text": "dos"}])


def test_mutantes_tenant_thresholds_mensaje_y_sql_exactos() -> None:
    with pytest.raises(
        ValueError,
        match=r"^El umbral de confianza debe estar en el rango \[0\.0, 1\.0\], recibido: 1\.5$",
    ):
        obtener_umbral_confianza_tenant(None, "t", override_umbral=1.5)

    conn, cur = _conn_con_fila((0.6,))
    conn.closed = False
    assert obtener_umbral_confianza_tenant(conn, "t_sql") == 0.6
    sql_ejecutado, params = cur.execute.call_args[0]
    assert sql_ejecutado.strip().startswith("SELECT COALESCE(")
    assert "link_confidence_threshold" in sql_ejecutado
    assert "core.organizations" in sql_ejecutado
    assert params == (0.75, "t_sql")


# --- ControlPresupuestoDocs (KC-CM-08-6): contenido exacto de lo que se expone -------------------------
def test_resumen_del_control_documental_expone_cada_campo() -> None:
    from kallicode_codemapping.budget import ControlPresupuestoDocs

    reloj = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    c = ControlPresupuestoDocs(tokens_max=1000, segundos_max=60.0, reloj=lambda: next(reloj))
    c.registrar_estimacion(tokens=400, documentos=3)
    c.autorizar(100)
    c.registrar_real(tokens_reales=120, tokens_estimados=100, duracion_ms=25)
    c.documentos_sin_procesar = 2
    assert c.resumen() == {
        "documentos_estimados": 3,
        "tokens_estimados": 400,
        "tokens_reales": 120,
        "desviacion_pct": 20.0,
        "presupuesto_tokens": 1000,
        "presupuesto_segundos": 60.0,
        "duracion_ms": 25,
        "documentos_sin_procesar": 2,
        "presupuesto_documental_excedido": False,
        "codigo": None,
        "motivo": None,
    }
    assert c.autorizado_tokens == 100
    assert c.invocaciones == 1
    assert c.estimado_procesados == 100


def test_control_documental_marca_el_exceso_y_lo_expone() -> None:
    from kallicode_codemapping.budget import (
        CODIGO_DOCS_MB,
        CODIGO_PRESUPUESTO_TOKENS,
        ControlPresupuestoDocs,
        PresupuestoDocumentalExcedido,
    )

    c = ControlPresupuestoDocs(tokens_max=50, bytes_max=10, bytes_pendientes=11)
    c.registrar_estimacion(tokens=10, documentos=1)
    assert (c.excedido, c.codigo) == (True, CODIGO_DOCS_MB)
    assert "11 B" in (c.motivo or "") and "10 B" in (c.motivo or "")
    with pytest.raises(PresupuestoDocumentalExcedido) as exc:
        c.autorizar(1)
    assert exc.value.codigo == CODIGO_DOCS_MB
    assert str(exc.value).startswith(f"{CODIGO_DOCS_MB}: ")

    d = ControlPresupuestoDocs(tokens_max=50)
    d.autorizar(30)
    with pytest.raises(PresupuestoDocumentalExcedido) as exc2:
        d.autorizar(21)
    assert exc2.value.codigo == CODIGO_PRESUPUESTO_TOKENS
    assert "requiere 51 tokens" in exc2.value.detalle and "es 50" in exc2.value.detalle
    assert d.autorizado_tokens == 30
