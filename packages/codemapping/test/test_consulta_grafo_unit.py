"""ConsultaGrafoPostgres sin base de datos (KC-CM-09-4): cada método del protocolo delega en la
función de cm_queries que declara el contrato cm-grafo-v1, con el loader y la consulta.

La equivalencia real con el doble y el contrato se prueba en tests/test_contrato_consulta.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from kallicode_codemapping import cm_queries
from kallicode_codemapping.consulta_grafo import ConsultaGrafoPostgres
from kallicode_core import cm_grafo
from kallicode_core.cm_grafo import ConsultaGrafo
from kallicode_core.cm_impacto import ConsultaGrafoIn, ResolverNodosIn, TipoArista, VecinosIn


def test_cumple_el_protocolo() -> None:
    assert isinstance(ConsultaGrafoPostgres(object()), ConsultaGrafo)  # type: ignore[arg-type]


@pytest.mark.parametrize("operacion", [o.nombre for o in cm_grafo.OPERACIONES])
def test_cada_metodo_delega_en_la_funcion_del_contrato(
    operacion: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    funcion = cm_grafo.OPERACIONES_POR_NOMBRE[operacion].funcion
    llamadas: list[tuple[Any, ...]] = []

    def falsa(*args: Any) -> str:
        llamadas.append(args)
        return "respuesta"

    monkeypatch.setattr(cm_queries, funcion, falsa)
    loader = object()
    consultas: dict[str, Any] = {
        "resolver_nodos": ResolverNodosIn(ids=["a"]),
        "vecinos": VecinosIn(nodo="a", direccion="entrante", tipos=[TipoArista.CALLS]),
    }
    consulta = consultas.get(operacion, ConsultaGrafoIn(nodos=["a"]))

    assert getattr(ConsultaGrafoPostgres(loader), operacion)(consulta) == "respuesta"  # type: ignore[arg-type]
    esperado: dict[str, tuple[Any, ...]] = {
        "resolver_nodos": (loader, ["a"]),
        "vecinos": (loader, "a", "entrante", [TipoArista.CALLS]),
    }
    assert llamadas == [esperado.get(operacion, (loader, consulta))]
