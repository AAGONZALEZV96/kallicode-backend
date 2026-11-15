"""Vigencia de los embeddings y métricas vectoriales sin base de datos (KC-CM-11-4).

Las pruebas contra PostgreSQL (texto canónico SQL = Python, detección y reparación reales, backfill,
refresco diario) están en tests/test_vigencia_embeddings_pg.py.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kallicode_codemapping import metricas, vigencia, worker
from kallicode_codemapping.embeddings import (
    hash_texto_embedding,
    texto_embedding_definicion,
    texto_embedding_simbolo,
)
from kallicode_codemapping.graph_loader import texto_hash
from kallicode_codemapping.worker import ConfigWorker, Procesador

from .test_worker_unit import AlmacenFalso, RedisConLecturas, Reloj


# --- texto canónico ---------------------------------------------------------------
def test_el_texto_canonico_es_el_de_siempre() -> None:
    """La fórmula no cambia respecto a la que usaban build_graph y refresh: los hashes ya
    guardados siguen siendo válidos y no hay que reembeber nada por el cambio."""
    assert texto_embedding_simbolo("f", "def f()", "Hace algo.") == "f def f() Hace algo."
    assert texto_embedding_simbolo("f", "def f()", None) == "f def f()"
    assert texto_embedding_simbolo("f", "def f()", "") == "f def f()"
    assert (
        texto_embedding_definicion("Factura", "Documento de cobro") == "Factura Documento de cobro"
    )
    assert texto_embedding_definicion("Factura", None) == "Factura"
    nombre, firma, desc = "a", "def a()", " x "
    assert texto_embedding_simbolo(nombre, firma, desc) == f"{nombre} {firma} {desc}".strip()


def test_graph_loader_usa_el_mismo_hash() -> None:
    assert texto_hash("abc") == hash_texto_embedding("abc")
    assert len(hash_texto_embedding("abc")) == 32


# --- reparar_vigencia ---------------------------------------------------------------
class LoaderFalso:
    tenant_id = "org_unit"

    def __init__(self, candidatos: int, nodos: int) -> None:
        self.candidatos: list[dict[str, Any]] = [
            {
                "id": f"v{i}",
                "layer": "code",
                "node_ref": f"s{i}",
                "text": f"t{i}",
                "payload": {},
                "motivo": "modelo" if i % 2 else "obsoleto",
            }
            for i in range(candidatos)
        ]
        self.nodos = nodos
        self.escritos: list[dict[str, Any]] = []
        self.pedidos: list[int] = []

    def embeddings_a_reparar(self, limite: int, modelo: str = "bge-m3") -> list[dict[str, Any]]:
        self.pedidos.append(limite)
        return self.candidatos[:limite]

    def upsert_vectors(self, items: list[dict[str, Any]], modelo: str = "bge-m3") -> None:
        self.escritos.extend(items)
        reparados = {i["node_ref"] for i in items}
        self.candidatos = [c for c in self.candidatos if c["node_ref"] not in reparados]

    def vigencia_embeddings(self, modelo: str = "bge-m3") -> list[dict[str, Any]]:
        pendientes = len(self.candidatos)
        return [{"capa": "symbol", "nodos": self.nodos, "vigentes": self.nodos - pendientes}]


class TEIContador:
    def __init__(self) -> None:
        self.lotes: list[list[str]] = []

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.lotes.append(texts)
        return [[0.0] * 1024 for _ in texts]


def test_repara_hasta_el_limite_y_avisa_de_los_pendientes() -> None:
    loader, tei = LoaderFalso(candidatos=7, nodos=100), TEIContador()
    res = vigencia.reparar_vigencia(loader, tei, limite=5, tamano_lote=2)
    assert loader.pedidos == [6]  # uno más que el límite para saber si quedan
    assert res.reembebidos == 5 and res.pendientes
    assert [len(lote) for lote in tei.lotes] == [2, 2, 1]
    assert all(len(i["vector"]) == 1024 for i in loader.escritos)
    assert res.motivos == {"obsoleto": 3, "modelo": 2}
    assert res.ratio == pytest.approx(0.98) and not res.cumple


def test_sin_pendientes_la_vigencia_cumple_el_umbral() -> None:
    loader = LoaderFalso(candidatos=3, nodos=100)
    res = vigencia.reparar_vigencia(loader, TEIContador(), limite=10)
    assert (res.reembebidos, res.pendientes, res.ratio, res.cumple) == (3, False, 1.0, True)


def test_limite_cero_solo_mide() -> None:
    loader, tei = LoaderFalso(candidatos=3, nodos=100), TEIContador()
    res = vigencia.reparar_vigencia(loader, tei, limite=0)
    assert loader.pedidos == [] and tei.lotes == [] and res.reembebidos == 0
    assert res.ratio == pytest.approx(0.97)


def test_sin_nodos_la_vigencia_es_completa() -> None:
    res = vigencia.ResultadoVigencia(capas=[])
    assert res.ratio == 1.0 and res.cumple


def test_la_reparacion_alimenta_el_contador_y_el_histograma() -> None:
    antes = (
        metricas.REGISTRY_CODEMAPPING.get_sample_value(
            "kc_codemapping_embeddings_reembebidos_total", {"motivo": "obsoleto"}
        )
        or 0.0
    )
    cuenta = (
        metricas.REGISTRY_CODEMAPPING.get_sample_value(
            "kc_codemapping_embeddings_vigencia_tras_refresco_count"
        )
        or 0.0
    )
    vigencia.reparar_vigencia(LoaderFalso(candidatos=4, nodos=50), TEIContador(), limite=10)
    assert (
        metricas.REGISTRY_CODEMAPPING.get_sample_value(
            "kc_codemapping_embeddings_reembebidos_total", {"motivo": "obsoleto"}
        )
        == antes + 2
    )
    assert (
        metricas.REGISTRY_CODEMAPPING.get_sample_value(
            "kc_codemapping_embeddings_vigencia_tras_refresco_count"
        )
        == cuenta + 1
    )


# --- métricas vectoriales --------------------------------------------------------------
class ConexionFalsa:
    def __init__(self, hnsw: list[tuple[Any, ...]], vig: list[tuple[Any, ...]]) -> None:
        self.hnsw, self.vig = hnsw, vig

    def execute(self, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
        return self.hnsw if "estado_hnsw" in sql else self.vig


def test_actualizar_metricas_publica_tamano_hinchazon_y_vigencia() -> None:
    conn = ConexionFalsa(
        hnsw=[
            ("idx_a", 8_000_000, 12.5, 3.0, 600.0),
            ("idx_sin_base", 1_000, None, 0.0, None),  # sin línea base: no se publica la hinchazón
        ],
        vig=[("symbol", 0.995, 4, 1, 0), ("definition", 0.97, 6, 0, 2)],
    )
    metricas.actualizar_metricas_vectoriales(conn)
    r = metricas.REGISTRY_CODEMAPPING.get_sample_value
    assert r("kc_codemapping_hnsw_tamano_bytes", {"indice": "idx_a"}) == 8_000_000
    assert r("kc_codemapping_hnsw_hinchazon_porcentaje", {"indice": "idx_a"}) == 12.5
    assert r("kc_codemapping_hnsw_tuplas_muertas_porcentaje", {"indice": "idx_a"}) == 3.0
    assert r("kc_codemapping_hnsw_segundos_desde_vacuum", {"indice": "idx_a"}) == 600.0
    assert r("kc_codemapping_hnsw_hinchazon_porcentaje", {"indice": "idx_sin_base"}) is None
    assert r("kc_codemapping_embeddings_vigencia_ratio", {"capa": "symbol"}) == 0.995
    assert (
        r("kc_codemapping_embeddings_no_vigentes", {"capa": "definition", "estado": "obsoleto"})
        == 6
    )
    assert r("kc_codemapping_embeddings_tenants_bajo_umbral", {"capa": "definition"}) == 2


def test_las_metricas_vectoriales_no_llevan_tenant_en_las_etiquetas() -> None:
    for familia in metricas.REGISTRY_CODEMAPPING.collect():
        for muestra in familia.samples:
            assert "tenant_id" not in muestra.labels


def test_un_fallo_al_leer_las_metricas_no_detiene_al_worker() -> None:
    with patch("psycopg.connect", side_effect=OSError("sin base")):
        assert metricas.refrescar_metricas_vectoriales("postgresql+psycopg://x@nohost/db") is False


def test_el_dsn_estilo_sqlalchemy_se_convierte_a_libpq() -> None:
    conn = MagicMock()
    conn.__enter__.return_value = ConexionFalsa([], [])
    with patch("psycopg.connect", return_value=conn) as conectar:
        assert metricas.refrescar_metricas_vectoriales("postgresql+psycopg://u:p@h:5432/db")
    assert conectar.call_args.args[0] == "postgresql://u:p@h:5432/db"


def test_el_bucle_lee_las_metricas_vectoriales_una_vez_por_intervalo() -> None:
    parar = threading.Event()
    redis = RedisConLecturas([[], [], []], parar)
    reloj = Reloj()
    cfg = ConfigWorker(bloqueo_ms=10, metricas_vectoriales_cada_s=300)
    p = Procesador(redis, AlmacenFalso(), MagicMock(), cfg, "test-1", reloj)  # type: ignore[arg-type]
    with patch.object(worker, "refrescar_metricas_vectoriales") as refrescar:
        worker.ciclo(p, parar)
    # Varias vueltas en el mismo instante del reloj falso: solo la primera lee la base.
    refrescar.assert_called_once_with(cfg.store_database_url)


def test_intervalo_cero_desactiva_la_lectura() -> None:
    parar = threading.Event()
    redis = RedisConLecturas([[]], parar)
    cfg = ConfigWorker(bloqueo_ms=10, metricas_vectoriales_cada_s=0)
    p = Procesador(redis, AlmacenFalso(), MagicMock(), cfg, "test-1", Reloj())  # type: ignore[arg-type]
    with patch.object(worker, "refrescar_metricas_vectoriales") as refrescar:
        worker.ciclo(p, parar)
    refrescar.assert_not_called()
