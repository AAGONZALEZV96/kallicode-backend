"""Cliente real del contrato cm-grafo-v1 sobre PostgreSQL (KC-CM-09-4).

`ConsultaGrafoPostgres` implementa `kallicode_core.cm_grafo.ConsultaGrafo` delegando en las
funciones de `cm_queries` (cada operación del contrato declara la suya). Corre en las transacciones
de lectura de `GraphLoader.fase()`, confinadas al tenant del loader. Su doble de prueba es
`kallicode_core.cm_grafo_doble.ConsultaGrafoDoble`; la misma suite de contrato corre contra los dos
(packages/codemapping/tests/test_contrato_consulta.py).

    with GraphLoader(settings=settings) as loader:
        grafo: ConsultaGrafo = ConsultaGrafoPostgres(loader)
        grafo.impacto(ConsultaGrafoIn(ficheros=["src/billing/invoice.py"], k=3))
"""

from __future__ import annotations

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

from kallicode_codemapping import cm_queries
from kallicode_codemapping.cm_queries import CursorFabrica


class ConsultaGrafoPostgres:
    """`ConsultaGrafo` sobre el esquema cm, para el tenant del loader."""

    def __init__(self, loader: CursorFabrica) -> None:
        self.loader = loader

    def resolver_nodos(self, consulta: ResolverNodosIn) -> list[NodoGrafo]:
        return cm_queries.resolver_nodos(self.loader, consulta.ids)

    def semillas(self, consulta: ConsultaGrafoIn) -> list[NodoGrafo]:
        return cm_queries.semillas(self.loader, consulta)

    def impacto(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return cm_queries.impacto(self.loader, consulta)

    def dependencias(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return cm_queries.dependencias(self.loader, consulta)

    def subgrafo(self, consulta: ConsultaGrafoIn) -> SubgrafoOut:
        return cm_queries.subgrafo(self.loader, consulta)

    def vecinos(self, consulta: VecinosIn) -> list[Arista]:
        return cm_queries.vecinos(self.loader, consulta.nodo, consulta.direccion, consulta.tipos)

    def dependencias_ocultas(self, consulta: ConsultaGrafoIn) -> DependenciasOcultasOut:
        return cm_queries.dependencias_ocultas(self.loader, consulta)

    def buscar_hibrido(self, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
        return cm_queries.buscar_hibrido(self.loader, consulta)

    def resolver_nuggets(self, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
        return cm_queries.resolver_nuggets(self.loader, consulta)

    def resolver_incidentes(self, consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut:
        return cm_queries.resolver_incidentes(self.loader, consulta)
