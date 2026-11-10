"""Cliente psycopg del contrato cm-consulta-v1 sin base de datos (KC-CM-08-3).

La semántica real (aprobados, tenant, orden, latencia) se prueba contra Postgres en
tests/test_cm_queries.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from kallicode_codemapping import cm_queries
from kallicode_codemapping.cm_queries import contexto_plantilla, resolver_nuggets
from kallicode_codemapping.nuggets import renderizar_nuggets_para_contexto
from kallicode_core import cm_consulta
from kallicode_core.cm_consulta import ConsultaNuggetsIn

COLUMNAS = ("id", "regla", "tipo", "seccion", "evidencia_ref", "prioridad", "score")


def _fila(i: int, prioridad: int = 1) -> tuple[Any, ...]:
    return (f"ng_{i}", f"Regla {i}", "regla_negocio", "S", f"c{i}", prioridad, 0.5)


class _Cursor:
    def __init__(self, loader: _Loader) -> None:
        self.loader = loader
        self.description: list[Any] = []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.loader.ejecutadas.append((sql, params))
        self.description = [SimpleNamespace(name=c) for c in COLUMNAS]

    def fetchall(self) -> list[tuple[Any, ...]]:
        variable = "module_conventions" if self.loader.ejecutadas[-1][1]["prefijo"] else "otra"
        return self.loader.filas.get(variable, self.loader.filas.get("*", []))


class _Loader:
    def __init__(self, filas: dict[str, list[tuple[Any, ...]]] | None = None) -> None:
        self.filas = filas or {}
        self.ejecutadas: list[tuple[str, dict[str, Any]]] = []
        self.fases: list[str] = []

    @contextmanager
    def fase(self, nombre: str) -> Iterator[_Cursor]:
        self.fases.append(nombre)
        yield _Cursor(self)


def test_sql_psycopg_es_la_de_core_con_otra_sintaxis_de_parametros() -> None:
    nucleo = cm_consulta.SQL_NUGGETS_APLICABLES
    psy = cm_queries.SQL_NUGGETS_APLICABLES
    assert re.sub(r"%\((\w+)\)s", r":\1", psy) == nucleo
    assert not re.search(r"(?<![:\w]):\w", psy)  # no queda ningún :parametro
    assert "%" not in psy.replace("%(", "")  # sin % literales que psycopg malinterprete
    usados = set(re.findall(r"%\((\w+)\)s", psy))
    consulta = ConsultaNuggetsIn(variable="nuggets")
    assert usados == set(cm_consulta.parametros_sql(consulta))


def test_resolver_usa_una_fase_de_lectura_y_construye_la_respuesta_del_contrato() -> None:
    loader = _Loader({"*": [_fila(1), _fila(2, prioridad=3)]})
    consulta = ConsultaNuggetsIn(variable="nuggets", ficheros=["a.py"])

    r = resolver_nuggets(loader, consulta)

    assert loader.fases == ["lectura"]
    sql, params = loader.ejecutadas[0]
    assert sql == cm_queries.SQL_NUGGETS_APLICABLES
    assert params == cm_consulta.parametros_sql(consulta)
    assert [(n.id, n.relevancia) for n in r.nuggets] == [("ng_1", "directa"), ("ng_2", "general")]
    assert r.texto == "- Regla 1\n- Regla 2"


def test_contexto_plantilla_resuelve_solo_las_variables_con_area() -> None:
    loader = _Loader({"*": [_fila(1)], "module_conventions": [_fila(7)]})

    solo_nuggets = contexto_plantilla(loader, ficheros=["a.py"])
    assert solo_nuggets == {
        "nuggets": "- Regla 1",
        "module_conventions": "",
        "nuggets_for_file": "",
    }
    assert len(loader.ejecutadas) == 1

    todas = contexto_plantilla(loader, nodos=["n"], modulo="src/billing", fichero="a.py",
                               presupuesto_caracteres=500)  # fmt: skip
    assert todas == {
        "nuggets": "- Regla 1",
        "module_conventions": "- Regla 7",
        "nuggets_for_file": "- Regla 1",
    }
    assert len(loader.ejecutadas) == 4  # 1 + nuggets, module_conventions y nuggets_for_file
    assert [p["prefijo"] for _, p in loader.ejecutadas[1:]] == [None, "src/billing/%", None]


def test_sin_filas_todas_las_variables_quedan_vacias() -> None:
    loader = _Loader()
    assert contexto_plantilla(loader, ficheros=["a.py"], modulo="m", fichero="a.py") == {
        "nuggets": "",
        "module_conventions": "",
        "nuggets_for_file": "",
    }


def test_renderizado_del_extractor_y_del_contrato_es_el_mismo() -> None:
    reglas = ["  Regla   uno  ", "", "z" * 281, "Regla dos", "Regla tres"]
    elegidas = cm_consulta.seleccionar_para_presupuesto(reglas, 24)
    assert renderizar_nuggets_para_contexto(reglas, 24) == cm_consulta.renderizar(
        [reglas[i] for i in elegidas]
    )
