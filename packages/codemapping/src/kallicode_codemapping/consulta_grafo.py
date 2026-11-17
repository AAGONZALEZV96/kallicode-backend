"""Cliente real del contrato cm-grafo-v1 sobre PostgreSQL (KC-CM-09-4).

`ConsultaGrafoPostgres` implementa `kallicode_core.cm_grafo.ConsultaGrafo` delegando en las
funciones de `cm_queries` (cada operación del contrato declara la suya). Corre en las transacciones
de lectura de `GraphLoader.fase()`, confinadas al tenant del loader. Su doble de prueba es
`kallicode_core.cm_grafo_doble.ConsultaGrafoDoble`; la misma suite de contrato corre contra los dos
(packages/codemapping/tests/test_contrato_consulta.py).

Una caída de PostgreSQL (conexión rechazada, servidor reiniciado, statement_timeout) sale como
`AppError` DEPENDENCIA_NO_DISPONIBLE (503) y no como la excepción de psycopg: el agente que consulta
la trata igual que la respuesta de la API interna (KC-CM-11-5).

    with GraphLoader(settings=settings) as loader:
        grafo: ConsultaGrafo = ConsultaGrafoPostgres(loader)
        grafo.impacto(ConsultaGrafoIn(ficheros=["src/billing/invoice.py"], k=3))
"""

from __future__ import annotations

from collections.abc import Callable

from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from kallicode_core.cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut
from kallicode_core.cm_impacto import (
    Arista,
    ConsultaGrafoIn,
    DependenciasOcultasOut,
    NodoGrafo,
    RecorridoOut,
    ResolverNodosIn,
    SubgrafoOut,
    VecinosIn,
)
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, ConsultaIncidentesOut
from kallicode_core.errors import AppError, fallo_de_dependencia

from kallicode_codemapping import cm_queries
from kallicode_codemapping.cm_queries import CursorFabrica


def tipificar_caidas[T](llamada: Callable[[], T]) -> T:
    """Ejecuta `llamada` y convierte una caída de la base en el AppError del catálogo."""
    try:
        return llamada()
    except AppError:
        raise
    except Exception as exc:
        error = fallo_de_dependencia(exc)
        if error is None:
            raise
        raise error from exc


class ConsultaGrafoPostgres:
    """`ConsultaGrafo` sobre el esquema cm, para el tenant del loader."""

    def __init__(self, loader: CursorFabrica) -> None:
        self.loader = loader

    def resolver_nodos(self, consulta: ResolverNodosIn) -> list[NodoGrafo]:
        return tipificar_caidas(lambda: cm_queries.resolver_nodos(self.loader, consulta.ids))

    def semillas(self, consulta: ConsultaGrafoIn) -> list[NodoGrafo]:
        return tipificar_caidas(lambda: cm_queries.semillas(self.loader, consulta))

    def impacto(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return tipificar_caidas(lambda: cm_queries.impacto(self.loader, consulta))

    def dependencias(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return tipificar_caidas(lambda: cm_queries.dependencias(self.loader, consulta))

    def subgrafo(self, consulta: ConsultaGrafoIn) -> SubgrafoOut:
        return tipificar_caidas(lambda: cm_queries.subgrafo(self.loader, consulta))

    def vecinos(self, consulta: VecinosIn) -> list[Arista]:
        return tipificar_caidas(
            lambda: cm_queries.vecinos(
                self.loader, consulta.nodo, consulta.direccion, consulta.tipos
            )
        )

    def dependencias_ocultas(self, consulta: ConsultaGrafoIn) -> DependenciasOcultasOut:
        return tipificar_caidas(lambda: cm_queries.dependencias_ocultas(self.loader, consulta))

    def buscar_hibrido(self, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
        return tipificar_caidas(lambda: cm_queries.buscar_hibrido(self.loader, consulta))

    def resolver_nuggets(self, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
        return tipificar_caidas(lambda: cm_queries.resolver_nuggets(self.loader, consulta))

    def resolver_incidentes(self, consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut:
        return tipificar_caidas(lambda: cm_queries.resolver_incidentes(self.loader, consulta))
