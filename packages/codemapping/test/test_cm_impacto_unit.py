"""Cliente psycopg de las consultas de recorrido del grafo sin base de datos (KC-CM-09-1).

Comprueba la traducción de parámetros, que cada función abre una fase de lectura con
statement_timeout y que construye las respuestas del contrato. La semántica real se prueba
contra PostgreSQL en tests/test_impacto.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from kallicode_codemapping import cm_queries
from kallicode_core import cm_impacto
from kallicode_core.cm_impacto import ConsultaGrafoIn, TipoArista

_NODO = ("id", "kind", "distancia", "via_tipo")
_ARISTA = ("tipo", "src_kind", "src_id", "dst_kind", "dst_id")


class _Cursor:
    def __init__(self, loader: _Loader) -> None:
        self.loader = loader
        self.description: list[Any] = []
        self._filas: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any) -> None:
        self.loader.ejecutadas.append((sql, params))
        nombre = next(
            (n for n, s in cm_queries.CONSULTAS_IMPACTO.items() if s == sql), "set_config"
        )
        columnas, self._filas = self.loader.respuestas.get(nombre, ((), []))
        self.description = [SimpleNamespace(name=c) for c in columnas]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._filas


class _Loader:
    def __init__(self, **respuestas: tuple[tuple[str, ...], list[tuple[Any, ...]]]) -> None:
        self.respuestas = respuestas
        self.ejecutadas: list[tuple[str, Any]] = []
        self.fases: list[str] = []

    @contextmanager
    def fase(self, nombre: str) -> Iterator[_Cursor]:
        self.fases.append(nombre)
        yield _Cursor(self)

    def nombres(self) -> list[str]:
        inverso = {s: n for n, s in cm_queries.CONSULTAS_IMPACTO.items()}
        return [inverso.get(sql, "timeout") for sql, _ in self.ejecutadas]


def test_las_ocho_consultas_son_las_de_core_con_parametros_psycopg() -> None:
    assert set(cm_queries.CONSULTAS_IMPACTO) == set(cm_impacto.CONSULTAS)
    for nombre, psy in cm_queries.CONSULTAS_IMPACTO.items():
        assert re.sub(r"%\((\w+)\)s", r":\1", psy) == cm_impacto.CONSULTAS[nombre]
        assert not re.search(r"(?<![:\w]):\w", psy), nombre
        assert "%" not in re.sub(r"%\(\w+\)s", "", psy), nombre


def test_impacto_y_dependencias_leen_con_timeout_y_construyen_el_contrato() -> None:
    filas = [("a", "symbol", 0, None), ("b", "symbol", 1, "CALLS")]
    loader = _Loader(impacto=(_NODO, filas), dependencias=(_NODO, filas))
    consulta = ConsultaGrafoIn(nodos=["a"], k=2)

    r = cm_queries.impacto(loader, consulta)
    assert (r.direccion, r.semillas) == ("entrante", ["a"])
    assert [(n.id, n.via_tipo) for n in r.nodos] == [("a", None), ("b", TipoArista.CALLS)]
    d = cm_queries.dependencias(loader, consulta)
    assert d.direccion == "saliente"

    assert loader.fases == ["lectura", "lectura"]
    assert loader.nombres() == ["timeout", "impacto", "timeout", "dependencias"]
    sql_timeout, params_timeout = loader.ejecutadas[0]
    assert "statement_timeout" in sql_timeout
    assert params_timeout == (str(cm_impacto.TIEMPO_MAXIMO_MS),)
    assert loader.ejecutadas[1][1] == cm_impacto.parametros_recorrido(consulta, "entrante")


def test_subgrafo_pide_las_aristas_de_los_nodos_alcanzados_en_la_misma_fase() -> None:
    loader = _Loader(
        subgrafo_nodos=(_NODO, [("a", "symbol", 0, None), ("t", "db_table", 1, "USES_TABLE")]),
        aristas_entre=(_ARISTA, [("USES_TABLE", "symbol", "a", "db_table", "t")]),
    )
    s = cm_queries.subgrafo(loader, ConsultaGrafoIn(nodos=["a"], k=1, tipos=["USES_TABLE"]))  # type: ignore[list-item]
    assert loader.fases == ["lectura"]
    assert loader.nombres() == ["timeout", "subgrafo_nodos", "aristas_entre"]
    params_aristas = loader.ejecutadas[2][1]
    assert params_aristas == {
        "ids": ["a", "t"],
        "tipos": ["USES_TABLE"],
        "limite": cm_impacto.MAX_ARISTAS + 1,
    }
    assert [n.id for n in s.nodos] == ["a", "t"]
    assert [(a.src_id, a.dst_id) for a in s.aristas] == [("a", "t")]


def test_vecinos_resolver_y_semillas() -> None:
    loader = _Loader(
        vecinos=(_ARISTA, [("CALLS", "symbol", "x", "symbol", "a")]),
        resolver_nodos=(("id", "kind"), [("a", "symbol")]),
        semillas=(("id", "kind"), [("f", "file"), ("s", "symbol")]),
    )
    v = cm_queries.vecinos(loader, "a", "entrante", ["CALLS"])
    assert [(x.tipo, x.src_id) for x in v] == [(TipoArista.CALLS, "x")]
    assert loader.ejecutadas[-1][1] == cm_impacto.parametros_vecinos("a", "entrante", ["CALLS"])

    assert [n.id for n in cm_queries.resolver_nodos(loader, ["a", "no"])] == ["a"]
    assert loader.ejecutadas[-1][1] == {"nodos": ["a", "no"]}

    sem = cm_queries.semillas(loader, ConsultaGrafoIn(ficheros=["src/f.py"]))
    assert [(n.id, n.kind) for n in sem] == [("f", "file"), ("s", "symbol")]
    assert loader.ejecutadas[-1][1] == {"nodos": [], "ficheros": ["src/f.py"]}


def test_dependencias_ocultas_informa_las_semillas_resueltas() -> None:
    loader = _Loader(
        semillas=(("id", "kind"), [("a", "symbol")]),
        dependencias_ocultas=(
            ("id", "kind", "tablas", "distancia"),
            [("h", "symbol", ["t2", "t1"], 2)],
        ),
    )
    r = cm_queries.dependencias_ocultas(loader, ConsultaGrafoIn(nodos=["a", "inexistente"]))
    assert loader.nombres() == ["timeout", "semillas", "dependencias_ocultas"]
    assert r.semillas == ["a"]
    assert [(d.id, d.tablas, d.distancia) for d in r.dependencias] == [("h", ["t1", "t2"], 2)]
