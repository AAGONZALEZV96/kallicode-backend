"""Recuperación en dos fases y duplicados, sin base de datos (KC-CM-09-5).

La semántica contra Postgres (orden real, tenant, umbral) está en tests/test_recuperacion_pg.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from kallicode_codemapping import recuperacion
from kallicode_codemapping.budget import estimar_tokens_texto
from kallicode_codemapping.config import build_settings
from kallicode_core import cm_recuperacion
from kallicode_core.cm_busqueda import BusquedaHibridaOut, CapaBusqueda, ResultadoBusqueda
from kallicode_core.cm_impacto import NodoAlcanzado, SubgrafoOut, TipoArista
from kallicode_core.cm_recuperacion import ElementoContexto, construir_duplicados


def _resultado(
    id_: str, similitud: float, *, capa: str = "symbol", contenido: str = "texto"
) -> ResultadoBusqueda:
    return ResultadoBusqueda(
        capa=CapaBusqueda(capa),
        id=f"de_{id_}",
        referencia=id_,
        modulo="src/billing",
        titulo=id_,
        contenido=contenido,
        estado_curacion=None,
        similitud=similitud,
    )


def _elemento(id_: str, contenido: str = "texto", puntuacion: float = 0.5) -> ElementoContexto:
    return ElementoContexto(
        capa="symbol",
        id=id_,
        referencia=id_,
        modulo=None,
        titulo=id_,
        contenido=contenido,
        similitud=0.5,
        distancia=None,
        puntuacion=puntuacion,
    )


# ---------------------------------------------------------------------------
# Puntuación
# ---------------------------------------------------------------------------
def test_proximidad_decae_con_los_saltos() -> None:
    assert [cm_recuperacion.proximidad(d) for d in (0, 1, 2, 3)] == [1.0, 0.5, 1 / 3, 0.25]
    assert cm_recuperacion.proximidad(None) == 0.0
    assert cm_recuperacion.proximidad(-1) == 0.0


def test_puntuacion_mezcla_similitud_y_cercania() -> None:
    assert cm_recuperacion.puntuacion(0.8, 1, 0.0) == 0.8  # w = 0: solo la fase 1
    assert cm_recuperacion.puntuacion(0.8, 1, 1.0) == 0.5  # w = 1: solo el grafo
    assert cm_recuperacion.puntuacion(0.8, None, 0.5) == 0.4
    assert cm_recuperacion.puntuacion(0.6, 0, 0.35) == round(0.65 * 0.6 + 0.35, 6)


@pytest.mark.parametrize("peso", [-0.01, 1.01])
def test_puntuacion_rechaza_pesos_fuera_de_rango(peso: float) -> None:
    with pytest.raises(ValueError, match="peso_topologico"):
        cm_recuperacion.puntuacion(0.5, 0, peso)


def test_reordenar_sube_lo_cercano_y_no_toca_doc_ni_nugget() -> None:
    candidatos = [
        _resultado("lejos", 0.90),
        _resultado("cerca", 0.70),
        _resultado("doc1", 0.72, capa="doc"),
    ]
    out = recuperacion.reordenar(candidatos, {"cerca": 0, "doc1": 0}, peso_topologico=0.35)
    # doc1 comparte referencia con una distancia dada, pero no es un nodo: no recibe cercanía.
    assert [e.referencia for e in out] == ["cerca", "lejos", "doc1"]
    por_ref = {e.referencia: e for e in out}
    assert por_ref["cerca"].distancia == 0
    assert por_ref["doc1"].distancia is None


def test_reordenar_desempata_por_similitud_capa_e_id() -> None:
    a = _resultado("b", 0.5)
    b = _resultado("a", 0.5)
    out = recuperacion.reordenar([a, b], {}, peso_topologico=0.0)
    assert [e.referencia for e in out] == ["a", "b"]


# ---------------------------------------------------------------------------
# Presupuesto de tokens
# ---------------------------------------------------------------------------
def test_ajustar_incluye_todo_si_cabe() -> None:
    elementos = [_elemento("a"), _elemento("b")]
    texto, incluidos, omitidos = recuperacion.ajustar_a_presupuesto(elementos, 1_000)
    assert [e.id for e in incluidos] == ["a", "b"] and omitidos == 0
    assert texto.splitlines() == ["- [symbol] a: texto", "- [symbol] b: texto"]


def test_ajustar_nunca_supera_el_presupuesto() -> None:
    elementos = [_elemento(f"e{i}", "palabra " * 60, 1 - i / 100) for i in range(30)]
    for presupuesto in (100, 250, 1_000, 4_000):
        texto, incluidos, omitidos = recuperacion.ajustar_a_presupuesto(elementos, presupuesto)
        assert estimar_tokens_texto(texto) <= presupuesto
        assert len(incluidos) + omitidos == len(elementos)
        assert omitidos > 0 or presupuesto == 4_000


def test_ajustar_omite_el_que_no_cabe_y_prueba_con_el_siguiente() -> None:
    grande = _elemento("grande", "x" * 1_000, 0.9)
    chico = _elemento("chico", "ok", 0.8)
    primero = _elemento("primero", "ok", 1.0)
    texto, incluidos, omitidos = recuperacion.ajustar_a_presupuesto(
        [primero, grande, chico], presupuesto_tokens=30
    )
    assert [e.id for e in incluidos] == ["primero", "chico"] and omitidos == 1
    assert "grande" not in texto


def test_ajustar_recorta_el_primero_si_ninguno_cabria() -> None:
    texto, incluidos, omitidos = recuperacion.ajustar_a_presupuesto(
        [_elemento("a", "palabra " * 500)], presupuesto_tokens=100
    )
    assert [e.id for e in incluidos] == ["a"] and omitidos == 0
    assert 0 < estimar_tokens_texto(texto) <= 100
    assert texto.endswith("…")


def test_ajustar_sin_elementos_devuelve_vacio() -> None:
    assert recuperacion.ajustar_a_presupuesto([], 500) == ("", [], 0)


def test_las_lineas_normalizan_espacios_y_recortan_contenido_largo() -> None:
    linea = recuperacion._linea(_elemento("a", "uno\n\n  dos\t tres"))
    assert linea == "- [symbol] a: uno dos tres"
    larga = recuperacion._linea(_elemento("a", "y" * 5_000))
    assert len(larga) < cm_recuperacion.CARACTERES_MAX_ELEMENTO + 40 and larga.endswith("…")


# ---------------------------------------------------------------------------
# contexto_diseno (con las consultas sustituidas)
# ---------------------------------------------------------------------------
class _Embebedor:
    def __init__(self, n: int = 1) -> None:
        self.n = n
        self.textos: list[str] = []

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.textos.extend(texts)
        return [[1.0] + [0.0] * 1023 for _ in range(self.n)]


def _parchear(
    monkeypatch: pytest.MonkeyPatch,
    candidatos: list[ResultadoBusqueda],
    distancias: dict[str, int],
) -> dict[str, Any]:
    visto: dict[str, Any] = {}

    def buscar(loader: Any, consulta: Any) -> BusquedaHibridaOut:
        visto["busqueda"] = consulta
        return BusquedaHibridaOut(resultados=candidatos, incluye_sin_curar=False)

    def subgrafo(loader: Any, consulta: Any) -> SubgrafoOut:
        visto["grafo"] = consulta
        nodos = [
            NodoAlcanzado(id=i, kind="symbol", distancia=d, via_tipo=None if d == 0 else "CALLS")
            for i, d in distancias.items()
        ]
        return SubgrafoOut(
            k=consulta.k,
            tipos=[TipoArista.CALLS],
            semillas=[n.id for n in nodos if n.distancia == 0],
            nodos=nodos,
            aristas=[],
            truncado=False,
        )

    monkeypatch.setattr(recuperacion.cm_queries, "buscar_hibrido", buscar)
    monkeypatch.setattr(recuperacion.cm_queries, "subgrafo", subgrafo)
    return visto


def test_contexto_usa_el_area_dada_y_pasa_los_parametros(monkeypatch: pytest.MonkeyPatch) -> None:
    visto = _parchear(
        monkeypatch,
        [_resultado("lejos", 0.9), _resultado("area", 0.7)],
        {"area": 0},
    )
    emb = _Embebedor()
    r = recuperacion.contexto_diseno(
        object(),  # type: ignore[arg-type]
        emb,
        "texto del ticket",
        ficheros=["src/a.py"],
        modulo="src/billing",
        k_amplio=30,
        saltos=3,
        peso_topologico=0.5,
    )
    assert emb.textos == ["texto del ticket"]
    assert visto["busqueda"].k == 30 and visto["busqueda"].modulo == "src/billing"
    assert visto["grafo"].ficheros == ["src/a.py"] and visto["grafo"].k == 3
    assert [e.referencia for e in r.elementos] == ["area", "lejos"]
    assert r.reordenado and r.semillas == ["area"] and r.peso_topologico == 0.5


def test_contexto_sin_area_usa_los_mejores_nodos_de_la_fase_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidatos = [_resultado(f"n{i}", 0.9 - i / 10) for i in range(5)]
    candidatos.insert(0, _resultado("docX", 0.95, capa="doc"))
    visto = _parchear(monkeypatch, candidatos, {"n0": 0})
    recuperacion.contexto_diseno(object(), _Embebedor(), "t")  # type: ignore[arg-type]
    assert visto["grafo"].nodos == ["n0", "n1", "n2"]  # 3 nodos; el doc no es un nodo


def test_contexto_sin_semillas_conserva_el_orden_de_la_fase_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visto = _parchear(monkeypatch, [_resultado("a", 0.9, capa="doc")], {})
    r = recuperacion.contexto_diseno(object(), _Embebedor(), "t")  # type: ignore[arg-type]
    assert "grafo" not in visto  # sin nodos candidatos no se consulta el grafo
    assert not r.reordenado and r.semillas == [] and r.peso_topologico == 0.0
    assert [e.referencia for e in r.elementos] == ["a"]


def test_contexto_respeta_el_presupuesto_del_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    candidatos = [_resultado(f"n{i}", 0.9, contenido="palabra " * 150) for i in range(20)]
    _parchear(monkeypatch, candidatos, {"n0": 0})
    cfg = build_settings(
        tenant_id="t", repo_url="x", diseno_presupuesto_tokens=400, diseno_saltos=1
    )
    r = recuperacion.contexto_diseno(object(), _Embebedor(), "t", settings=cfg)  # type: ignore[arg-type]
    assert r.presupuesto_tokens == 400 and r.saltos == 1
    assert r.tokens_estimados <= 400 and r.omitidos_por_presupuesto > 0
    assert r.candidatos == 20


@pytest.mark.parametrize(
    ("kw", "mensaje"),
    [
        ({"presupuesto_tokens": 10}, "presupuesto_tokens"),
        ({"saltos": 9}, "saltos"),
    ],
)
def test_contexto_valida_sus_parametros(kw: dict[str, int], mensaje: str) -> None:
    with pytest.raises(ValueError, match=mensaje):
        recuperacion.contexto_diseno(object(), _Embebedor(), "t", **kw)  # type: ignore[arg-type]


def test_contexto_rechaza_texto_vacio_y_embebedor_defectuoso() -> None:
    with pytest.raises(ValueError, match="vacío"):
        recuperacion.contexto_diseno(object(), _Embebedor(), "   ")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="2 vectores"):
        recuperacion.contexto_diseno(object(), _Embebedor(2), "t")  # type: ignore[arg-type]


def test_la_salida_no_admite_superar_el_presupuesto() -> None:
    with pytest.raises(ValueError, match="presupuesto"):
        cm_recuperacion.ContextoDisenoOut(
            texto="x",
            elementos=[],
            candidatos=0,
            omitidos_por_presupuesto=0,
            presupuesto_tokens=100,
            tokens_estimados=101,
            reordenado=False,
            semillas=[],
            saltos=2,
            peso_topologico=0.0,
        )


# ---------------------------------------------------------------------------
# Duplicados
# ---------------------------------------------------------------------------
def _fila(i: str, similitud: float) -> dict[str, Any]:
    return {
        "ticket_id": i,
        "numero": f"KC-{i}",
        "titulo": i,
        "etapa": "triage",
        "similitud": similitud,
    }


def test_duplicado_es_estrictamente_mayor_que_el_umbral() -> None:
    r = construir_duplicados(0.7, [_fila("a", 0.95), _fila("b", 0.7), _fila("c", 0.69)])
    assert [c.ticket_id for c in r.candidatos] == ["a", "b", "c"]
    assert [d.ticket_id for d in r.duplicados] == ["a"]
    assert r.umbral == 0.7


def test_la_similitud_se_acota_y_redondea() -> None:
    r = construir_duplicados(
        0.5, [_fila("a", 1.0000001), _fila("b", -0.0000001), _fila("c", 0.123456789)]
    )
    assert [c.similitud for c in r.candidatos] == [1.0, 0.0, 0.123457]


def test_umbral_fuera_de_rango_es_un_error() -> None:
    with pytest.raises(ValueError, match="umbral"):
        construir_duplicados(1.5, [])


class _Cursor:
    def __init__(self, loader: _Loader) -> None:
        self.loader = loader
        self.description: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.loader.ejecutadas.append((sql, params))
        if "FROM vec.ticket_embeddings" in sql:
            self.description = [SimpleNamespace(name=c) for c in _fila("x", 0)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [tuple(f.values()) for f in self.loader.filas]


class _Loader:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self.filas = filas
        self.ejecutadas: list[tuple[str, Any]] = []
        self.fases: list[str] = []

    @contextmanager
    def fase(self, nombre: str) -> Iterator[_Cursor]:
        self.fases.append(nombre)
        yield _Cursor(self)


def test_buscar_duplicados_configura_hnsw_dedup_y_pasa_los_parametros() -> None:
    loader = _Loader([_fila("a", 0.9), _fila("b", 0.5)])
    r = recuperacion.buscar_duplicados(
        loader,
        _Embebedor(),
        "texto",
        ticket_id="tk_1",
        umbral=0.8,
        k=7,  # type: ignore[arg-type]
    )
    assert loader.fases == ["dedup"]
    assert loader.ejecutadas[0][0] == "SELECT vec.configurar_busqueda('dedup')"
    _sql, params = loader.ejecutadas[1]
    assert params["k"] == 7 and params["excluir"] == "tk_1"
    assert params["embedding"].startswith("[1,0,0") and params["embedding"].endswith("]")
    assert [d.ticket_id for d in r.duplicados] == ["a"]


@pytest.mark.parametrize("k", [0, 51])
def test_buscar_duplicados_acota_k(k: int) -> None:
    with pytest.raises(ValueError, match="k debe"):
        recuperacion.buscar_duplicados(_Loader([]), _Embebedor(), "t", k=k)  # type: ignore[arg-type]


def test_buscar_duplicados_rechaza_texto_vacio() -> None:
    with pytest.raises(ValueError, match="vacío"):
        recuperacion.buscar_duplicados(_Loader([]), _Embebedor(), "  ")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
def test_la_sql_psycopg_es_la_del_contrato_con_otra_sintaxis() -> None:
    assert not re.search(r"(?<![:\w]):\w+", recuperacion.SQL_TICKETS_SIMILARES.replace("::", ""))
    assert "%(embedding)s" in recuperacion.SQL_TICKETS_SIMILARES
    reconstruida = re.sub(r"%\((\w+)\)s", r":\1", recuperacion.SQL_TICKETS_SIMILARES)
    assert reconstruida == cm_recuperacion.SQL_TICKETS_SIMILARES


def test_cada_tabla_de_la_consulta_de_duplicados_filtra_por_el_tenant_de_la_sesion() -> None:
    sql = cm_recuperacion.SQL_TICKETS_SIMILARES
    assert sql.count("core.current_tenant()") == 2
    assert "e.tenant_id = core.current_tenant()" in sql
    assert "t.tenant_id = core.current_tenant()" in sql
    assert "MATERIALIZED" in sql and "c.distancia + 0" in sql  # relaxed_order: se reordena fuera


def test_los_defectos_de_settings_coinciden_con_el_contrato() -> None:
    cfg = build_settings(tenant_id="t", repo_url="x")
    assert cfg.diseno_presupuesto_tokens == cm_recuperacion.PRESUPUESTO_TOKENS_DISENO_DEFECTO
    assert cfg.diseno_peso_topologico == cm_recuperacion.PESO_TOPOLOGICO_DEFECTO
    assert cfg.diseno_saltos == cm_recuperacion.SALTOS_DEFECTO
    assert cfg.dedup_umbral == cm_recuperacion.UMBRAL_DUPLICADO_DEFECTO
