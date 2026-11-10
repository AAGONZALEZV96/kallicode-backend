"""Búsqueda híbrida sin base de datos (KC-CM-08-4).

Contrato cm-busqueda-v1, cliente psycopg y arnés de evaluación P@5. La semántica real (tenant en el
plan, curación, filtros, P@5 frente a la línea base) se prueba contra Postgres en
tests/test_busqueda.py; la latencia con 200.000 vectores, en tests/bench_busqueda_hibrida.py.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kallicode_codemapping import cm_queries
from kallicode_codemapping import evaluacion_busqueda as ev
from kallicode_codemapping.cm_queries import buscar_hibrido, buscar_por_texto, precision_en_k
from kallicode_core import cm_busqueda
from kallicode_core.cm_busqueda import BusquedaHibridaIn, CapaBusqueda
from pydantic import ValidationError

MIGRACION = Path(__file__).resolve().parents[3] / "db" / "migrations" / "043_busqueda_hibrida.sql"
V = [0.0] * 1023 + [1.0]


# ---------------------------------------------------------------------------
# Contrato y coherencia con la migración 043
# ---------------------------------------------------------------------------
def test_las_capas_del_contrato_son_las_de_la_funcion_sql() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    capas = re.search(r"SELECT ARRAY\[([^\]]+)\]", sql)
    assert capas is not None
    assert re.findall(r"'(\w+)'", capas.group(1)) == [c.value for c in CapaBusqueda]
    assert set(ev.CAPAS) == {c.value for c in CapaBusqueda}


def test_las_cotas_del_contrato_son_las_de_la_funcion_sql() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    assert f"p_k > {cm_busqueda.K_MAXIMO}" in sql
    assert f"vector_dims(p_embedding) <> {cm_busqueda.DIMENSIONES}" in sql
    assert f"char_length(p_modulo) > {cm_busqueda.MODULO_MAX_CHARS}" in sql
    assert "vec.configurar_busqueda('diseno')" in sql  # iterative_scan calibrado (038)


def test_la_consulta_y_su_explain_comparten_una_unica_definicion() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    assert sql.count("cm.busqueda_hibrida_sql()") >= 3  # definición + dos usos
    assert sql.count("WITH chunks_modulo AS") == 1
    # Cada recorrido vectorial filtra por el tenant de forma explícita.
    for tabla in ("vec.definition_embeddings d", "vec.doc_chunks c"):
        for bloque in re.findall(rf"FROM {re.escape(tabla)}\s+WHERE (\w\.tenant_id = \$2)", sql):
            assert bloque.endswith("= $2")
    assert len(re.findall(r"FROM vec\.\w+ \w\s+WHERE \w\.tenant_id = \$2", sql)) == 3


def test_entrada_valida_dimensiones_k_y_modulo() -> None:
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=[0.1, 0.2])
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, k=0)
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, k=cm_busqueda.K_MAXIMO + 1)
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, capas=[])
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, capas=["inexistente"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, modulo=" / ")
    with pytest.raises(ValidationError):
        BusquedaHibridaIn(embedding=V, extra=1)  # type: ignore[call-arg]
    c = BusquedaHibridaIn(embedding=V, modulo=" src/billing/ ")
    assert c.modulo == "src/billing" and c.k == 10 and not c.incluir_sin_curar


def test_parametros_sql() -> None:
    c = BusquedaHibridaIn(embedding=V, capas=["doc", "concept"], modulo="src", k=7)
    p = cm_busqueda.parametros_sql(c)
    assert p["capas"] == ["doc", "concept"] and p["modulo"] == "src" and p["k"] == 7
    assert p["incluir_sin_curar"] is False
    assert p["embedding"].startswith("[0,0,") and p["embedding"].endswith(",1]")
    assert cm_busqueda.parametros_sql(BusquedaHibridaIn(embedding=V))["capas"] is None


def test_vector_literal_es_exacto_en_float4() -> None:
    valores = ev.embeber_referencia("facturación por convenio corporativo")
    literal = cm_busqueda.vector_literal(valores)
    leidos = [float(x) for x in literal.strip("[]").split(",")]
    assert ev._a_float32(leidos) == valores


def test_construir_respuesta_normaliza_y_acota_la_similitud() -> None:
    c = BusquedaHibridaIn(embedding=V, incluir_sin_curar=True)
    filas = [
        {
            "capa": "concept",
            "id": "de_c",
            "referencia": "c",
            "modulo": "src",
            "titulo": "C",
            "contenido": "x",
            "estado_curacion": "sin_curar",
            "similitud": 1.0000001,
        },
        {
            "capa": "doc",
            "id": "d",
            "referencia": "doc1",
            "modulo": None,
            "titulo": None,
            "contenido": None,
            "estado_curacion": None,
            "similitud": -0.2,
        },
    ]
    r = cm_busqueda.construir_respuesta(c, filas)
    assert r.version == "cm-busqueda-v1" and r.incluye_sin_curar
    assert [x.similitud for x in r.resultados] == [1.0, 0.0]
    assert r.resultados[0].capa is CapaBusqueda.CONCEPT
    assert r.resultados[1].titulo == "" and r.resultados[1].modulo is None


# ---------------------------------------------------------------------------
# Cliente psycopg
# ---------------------------------------------------------------------------
class _Cursor:
    def __init__(self, loader: _Loader) -> None:
        self.loader = loader
        self.description = [SimpleNamespace(name=c) for c in cm_busqueda.COLUMNAS]

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.loader.ejecutadas.append((sql, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.loader.filas


class _Loader:
    def __init__(self, filas: list[tuple[Any, ...]] | None = None) -> None:
        self.filas = filas or []
        self.ejecutadas: list[tuple[str, dict[str, Any]]] = []
        self.fases: list[str] = []

    @contextmanager
    def fase(self, nombre: str) -> Iterator[_Cursor]:
        self.fases.append(nombre)
        yield _Cursor(self)


class _Embebedor:
    def __init__(self, n: int = 1) -> None:
        self.n = n
        self.textos: list[list[str]] = []

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.textos.append(texts)
        return [V] * self.n


def test_sql_psycopg_es_la_de_core_con_otra_sintaxis_de_parametros() -> None:
    psy = cm_queries.SQL_BUSQUEDA_HIBRIDA
    assert re.sub(r"%\((\w+)\)s", r":\1", psy) == cm_busqueda.SQL_BUSQUEDA_HIBRIDA
    assert set(re.findall(r"%\((\w+)\)s", psy)) == set(
        cm_busqueda.parametros_sql(BusquedaHibridaIn(embedding=V))
    )
    assert "cm.buscar_hibrido(" in psy


def test_buscar_hibrido_usa_una_fase_y_devuelve_el_contrato() -> None:
    loader = _Loader([("doc", "d1", "doc_1", None, "Sección", "Texto", None, 0.8)])
    c = BusquedaHibridaIn(embedding=V, capas=["doc"])
    r = buscar_hibrido(loader, c)
    assert loader.fases == ["busqueda"]
    assert loader.ejecutadas == [(cm_queries.SQL_BUSQUEDA_HIBRIDA, cm_busqueda.parametros_sql(c))]
    assert [(x.capa, x.id, x.similitud) for x in r.resultados] == [(CapaBusqueda.DOC, "d1", 0.8)]


def test_buscar_por_texto_embebe_una_vez_y_traslada_los_filtros() -> None:
    loader, embebedor = _Loader(), _Embebedor()
    r = buscar_por_texto(
        loader,
        embebedor,
        "facturación",
        capas=["symbol"],
        modulo="src",
        k=3,
        incluir_sin_curar=True,
    )
    assert embebedor.textos == [["facturación"]]
    params = loader.ejecutadas[0][1]
    assert (params["capas"], params["modulo"], params["k"], params["incluir_sin_curar"]) == (
        ["symbol"],
        "src",
        3,
        True,
    )
    assert r.resultados == [] and r.incluye_sin_curar


def test_buscar_por_texto_rechaza_texto_vacio_capa_desconocida_y_respuesta_rara() -> None:
    with pytest.raises(ValueError, match="vacía"):
        buscar_por_texto(_Loader(), _Embebedor(), "   ")
    with pytest.raises(ValueError):
        buscar_por_texto(_Loader(), _Embebedor(), "x", capas=["inexistente"])
    with pytest.raises(ValueError, match="2 vectores"):
        buscar_por_texto(_Loader(), _Embebedor(n=2), "x")


@pytest.mark.parametrize(
    ("devueltos", "relevantes", "k", "esperado"),
    [
        (["a", "b", "c", "d", "e"], {"a", "b", "c", "d", "e"}, 5, 1.0),
        (["a", "x", "b", "y", "z"], {"a", "b"}, 5, 0.4),
        (["a", "b"], {"a", "b", "c"}, 5, 0.4),  # los huecos cuentan como fallo
        (["x", "a", "b", "c", "d", "e"], {"a", "b", "c", "d", "e"}, 5, 0.8),  # solo los k primeros
        ([], {"a"}, 5, 0.0),
        (["a"], set(), 1, 0.0),
    ],
)
def test_precision_en_k(
    devueltos: list[str], relevantes: set[str], k: int, esperado: float
) -> None:
    assert precision_en_k(devueltos, relevantes, k) == pytest.approx(esperado)


def test_precision_en_k_rechaza_k_no_positivo() -> None:
    with pytest.raises(ValueError):
        precision_en_k(["a"], {"a"}, 0)


# ---------------------------------------------------------------------------
# Embebedor de referencia
# ---------------------------------------------------------------------------
def test_embebedor_de_referencia_es_determinista_unitario_y_de_1024() -> None:
    a = ev.embeber_referencia("Facturación por convenio corporativo")
    assert a == ev.embeber_referencia("Facturación por convenio corporativo")
    assert len(a) == 1024
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-5)
    assert ev.embeber_referencia("de la el") == [1.0] + [0.0] * 1023  # solo palabras vacías
    assert ev.EmbebedorReferencia().embed_texts_sync(["a b", "c"]) == [
        ev.embeber_referencia("a b"),
        ev.embeber_referencia("c"),
    ]


def test_tokens_normalizan_acentos_mayusculas_camel_y_snake() -> None:
    assert ev.tokens("FacturadorConvenio.emitir_factura en la Facturación") == [
        "facturador",
        "convenio",
        "emitir",
        "factura",
        "facturacion",
    ]


def test_textos_parecidos_estan_mas_cerca_que_los_distintos() -> None:
    q = ev.embeber_referencia("facturación de convenios corporativos")
    cerca = ev.embeber_referencia("factura de un convenio corporativo")
    lejos = ev.embeber_referencia("bloqueo de cuenta por contraseña")
    assert ev.coseno(q, cerca) > ev.coseno(q, lejos)
    assert ev.coseno([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# Corpus, relevancia y línea base
# ---------------------------------------------------------------------------
def test_corpus_y_consultas_de_referencia() -> None:
    corpus = ev.cargar_corpus()
    consultas = ev.cargar_consultas()
    assert len(consultas) == 20
    assert {c.tema for c in consultas} == {e.tema for e in corpus.elementos.values()}
    assert all(ev.relevantes(corpus, c) for c in consultas)  # ninguna consulta sin respuesta
    capas = {e.capa for e in corpus.elementos.values()}
    assert capas == set(ev.CAPAS)
    estados = {(e.capa, e.estado) for e in corpus.elementos.values() if e.estado}
    assert {("concept", "sin_curar"), ("concept", "pendiente"), ("concept", "curador")} <= estados
    assert {("nugget", "propuesto"), ("nugget", "rechazado")} <= estados


def _corpus(tmp_path: Path, elementos: list[dict[str, Any]]) -> Path:
    ruta = tmp_path / "corpus.json"
    datos = {"version": "t", "temas": {"t": {"modulo": "src/t", "elementos": elementos}}}
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


DOC = {"id": "d", "capa": "doc", "texto": "x"}


@pytest.mark.parametrize(
    "elementos",
    [
        [{"id": "a", "capa": "otra", "texto": "x"}],
        [{"id": "a", "capa": "concept", "texto": "x", "estado": "raro"}],
        [{"id": "a", "capa": "nugget", "texto": "x", "estado": "aprobado"}],
        [{"id": "a", "capa": "nugget", "texto": "x", "estado": "aprobado", "chunk": "zz"}],
        [DOC, {"id": "a", "capa": "nugget", "texto": "x", "estado": "raro", "chunk": "d"}],
        [{"id": "a", "capa": "symbol", "texto": "x"}],
        [{"id": "a", "capa": "doc", "texto": "x", "enlaces": ["zz"]}],
        [DOC, DOC],
    ],
    ids=[
        "capa",
        "estado-concepto",
        "nugget-sin-chunk",
        "chunk-inexistente",
        "estado-nugget",
        "simbolo-sin-fichero",
        "enlace-roto",
        "id-repetido",
    ],
)
def test_cargar_corpus_rechaza_datos_incoherentes(
    tmp_path: Path, elementos: list[dict[str, Any]]
) -> None:
    with pytest.raises(ValueError):
        ev.cargar_corpus(_corpus(tmp_path, elementos))


def test_cargar_consultas_rechaza_ids_repetidos(tmp_path: Path) -> None:
    ruta = tmp_path / "c.json"
    ruta.write_text(
        json.dumps({"consultas": [{"id": "q", "tema": "t", "texto": "a"}] * 2}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        ev.cargar_consultas(ruta)


def _mini(tmp_path: Path) -> ev.Corpus:
    return ev.cargar_corpus(
        _corpus(
            tmp_path,
            [
                {"id": "f", "capa": "file", "titulo": "src/t/a.py", "texto": "fichero"},
                {"id": "s", "capa": "symbol", "fichero": "f", "texto": "simbolo"},
                {"id": "c1", "capa": "concept", "estado": "curado", "texto": "concepto"},
                {"id": "c2", "capa": "concept", "estado": "pendiente", "texto": "concepto"},
                {"id": "d", "capa": "doc", "enlaces": ["s"], "texto": "documento"},
                {"id": "dx", "capa": "doc", "texto": "documento suelto"},
                {"id": "n1", "capa": "nugget", "estado": "aprobado", "chunk": "d", "texto": "r"},
                {"id": "n2", "capa": "nugget", "estado": "propuesto", "chunk": "d", "texto": "r"},
                {"id": "n3", "capa": "nugget", "estado": "rechazado", "chunk": "d", "texto": "r"},
            ],
        )
    )


def test_relevancia_aplica_capas_modulo_y_curacion(tmp_path: Path) -> None:
    corpus = _mini(tmp_path)
    todo = ev.Consulta(id="q", tema="t", texto="x")
    assert ev.relevantes(corpus, todo) == {"f", "s", "c1", "d", "dx", "n1"}
    con_pendientes = ev.Consulta(id="q", tema="t", texto="x", incluir_sin_curar=True)
    assert ev.relevantes(corpus, con_pendientes) == {"f", "s", "c1", "c2", "d", "dx", "n1", "n2"}
    modulo = ev.Consulta(id="q", tema="t", texto="x", modulo="src/t")
    assert ev.relevantes(corpus, modulo) == {"f", "s", "c1", "d", "n1"}  # dx no está enlazado
    otro = ev.Consulta(id="q", tema="t", texto="x", modulo="src/tt")
    assert ev.relevantes(corpus, otro) == set()
    solo_doc = ev.Consulta(id="q", tema="t", texto="x", capas=("doc",))
    assert ev.relevantes(corpus, solo_doc) == {"d", "dx"}
    assert ev.relevantes(corpus, ev.Consulta(id="q", tema="otro", texto="x")) == set()


def test_ruta_de_nodo_por_capa(tmp_path: Path) -> None:
    corpus = _mini(tmp_path)
    assert ev._ruta_de_nodo(corpus, "f") == "src/t/a.py"
    assert ev._ruta_de_nodo(corpus, "s") == "src/t/a.py"
    assert ev._ruta_de_nodo(corpus, "c1") == ""


def test_ranking_referencia_desempata_por_capa_e_id(tmp_path: Path) -> None:
    corpus = _mini(tmp_path)
    vectores = ev.vectores_corpus(corpus, ev.EmbebedorReferencia())
    assert "n1" not in vectores  # un nugget usa el vector de su chunk
    consulta = ev.Consulta(id="q", tema="t", texto="x", capas=("doc", "nugget"))
    # El vector del chunk d: d y su nugget empatan a distancia 0; 'doc' < 'nugget'.
    assert ev.ranking_referencia(corpus, vectores, consulta, vectores["d"], k=2) == ["d", "n1"]


def test_la_linea_base_versionada_se_reproduce_exactamente() -> None:
    base = ev.cargar_linea_base()
    nueva = ev.generar_linea_base(
        ev.cargar_corpus(), ev.cargar_consultas(), ev.EmbebedorReferencia(), ev.EMBEBEDOR_REFERENCIA
    )
    base.pop("fecha")
    nueva.pop("fecha")
    assert nueva == base, "la línea base no corresponde al corpus: regenerarla (--actualizar)"
    assert 0 < base["p_at_5_media"] <= base["p_at_5_maxima_media"] <= 1


def test_main_imprime_o_actualiza_la_linea_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ev.main([]) == 0
    impreso = json.loads(capsys.readouterr().out)
    assert impreso["embebedor"] == ev.EMBEBEDOR_REFERENCIA

    destino = tmp_path / "linea.json"
    monkeypatch.setattr(ev, "RUTA_LINEA_BASE", destino)
    assert ev.main(["--actualizar"]) == 0
    assert "P@5 media" in capsys.readouterr().out
    assert json.loads(destino.read_text(encoding="utf-8"))["n_consultas"] == 20


def test_main_con_tei_mide_con_bge_m3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from kallicode_codemapping import embeddings

    llamadas: list[int] = []

    def falso(self: Any, texts: list[str]) -> list[list[float]]:
        llamadas.append(len(texts))
        return [ev.embeber_referencia(t) for t in texts]

    monkeypatch.setattr(embeddings.TEIEmbeddingClient, "embed_texts_sync", falso)
    assert ev.main(["--tei", "http://tei.invalid"]) == 0
    assert json.loads(capsys.readouterr().out)["embebedor"] == "bge-m3"
    assert max(llamadas) <= embeddings.TAMANO_LOTE_TEI  # por lotes que TEI acepta
