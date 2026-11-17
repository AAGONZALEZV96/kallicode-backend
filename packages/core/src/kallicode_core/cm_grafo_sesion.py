"""Contrato cm-grafo-v1 sobre una sesión async confinada al tenant (KC-CM-11-5).

`ConsultaGrafoSesion` es la versión SQLAlchemy async de
`kallicode_codemapping.consulta_grafo.ConsultaGrafoPostgres` (psycopg síncrono): la misma SQL
(`cm_impacto.CONSULTAS`, `cm_impacto.SQL_IMPACTO_RUTA`, `cm_busqueda`, `cm_consulta`,
`cm_incidentes`) y los mismos constructores de respuesta, así que las dos rutas no pueden
desviarse. La usa la API interna (`POST /internal/v1/codemapping/grafo/...`) con la sesión RLS de
`db.sesion_tenant`; la paridad con el cliente psycopg la comprueba
packages/codemapping/tests/test_resiliencia.py.

    async with sesion_tenant(tenant_id, actor="svc:orquestador") as db:
        recorrido = await ConsultaGrafoSesion(db).impacto(ConsultaGrafoIn(nodos=["sym_x"], k=3))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import cm_busqueda, cm_consulta, cm_impacto, cm_incidentes
from .cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from .cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut
from .cm_impacto import (
    Arista,
    ConsultaGrafoIn,
    DependenciasOcultasOut,
    Direccion,
    NodoGrafo,
    RecorridoOut,
    ResolverNodosIn,
    SubgrafoOut,
    VecinosIn,
)
from .cm_incidentes import ConsultaIncidentesIn, ConsultaIncidentesOut


class ConsultaGrafoSesion:
    """Las operaciones del contrato cm-grafo-v1 para el tenant de la sesión."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _filas(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        resultado = await self.db.execute(text(sql), dict(params))
        return [dict(f) for f in resultado.mappings().all()]

    async def _limitar_tiempo(self) -> None:
        await self.db.execute(
            text(cm_impacto.SQL_LIMITAR_TIEMPO), {"ms": str(cm_impacto.TIEMPO_MAXIMO_MS)}
        )

    # -- Grafo -------------------------------------------------------------------------------
    async def resolver_nodos(self, consulta: ResolverNodosIn) -> list[NodoGrafo]:
        filas = await self._filas(
            cm_impacto.CONSULTAS["resolver_nodos"], {"nodos": list(consulta.ids)}
        )
        return [NodoGrafo(id=str(f["id"]), kind=str(f["kind"])) for f in filas]

    async def semillas(self, consulta: ConsultaGrafoIn) -> list[NodoGrafo]:
        filas = await self._filas(
            cm_impacto.CONSULTAS["semillas"],
            {"nodos": list(consulta.nodos), "ficheros": list(consulta.ficheros)},
        )
        return [NodoGrafo(id=str(f["id"]), kind=str(f["kind"])) for f in filas]

    async def _recorrido_ruta(
        self, consulta: ConsultaGrafoIn, direccion: Direccion
    ) -> RecorridoOut:
        await self._limitar_tiempo()
        filas = await self._filas(
            cm_impacto.SQL_IMPACTO_RUTA, cm_impacto.parametros_impacto_ruta(consulta, direccion)
        )
        return cm_impacto.construir_recorrido_ruta(consulta, direccion, filas)

    async def impacto(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return await self._recorrido_ruta(consulta, "entrante")

    async def dependencias(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return await self._recorrido_ruta(consulta, "saliente")

    async def subgrafo(self, consulta: ConsultaGrafoIn) -> SubgrafoOut:
        params = cm_impacto.parametros_recorrido(consulta, "ambas")
        await self._limitar_tiempo()
        filas_nodos = await self._filas(cm_impacto.CONSULTAS["subgrafo_nodos"], params)
        ids = [str(f["id"]) for f in filas_nodos[: cm_impacto.MAX_NODOS]]
        filas_aristas = await self._filas(
            cm_impacto.CONSULTAS["aristas_entre"],
            {"ids": ids, "tipos": params["tipos"], "limite": cm_impacto.MAX_ARISTAS + 1},
        )
        return cm_impacto.construir_subgrafo(consulta, filas_nodos, filas_aristas)

    async def vecinos(self, consulta: VecinosIn) -> list[Arista]:
        filas = await self._filas(
            cm_impacto.CONSULTAS["vecinos"],
            cm_impacto.parametros_vecinos(consulta.nodo, consulta.direccion, consulta.tipos),
        )
        return cm_impacto.aristas(filas)

    async def dependencias_ocultas(self, consulta: ConsultaGrafoIn) -> DependenciasOcultasOut:
        params = cm_impacto.parametros_recorrido(consulta, "saliente")
        await self._limitar_tiempo()
        semillas = await self._filas(
            cm_impacto.CONSULTAS["semillas"],
            {"nodos": params["nodos"], "ficheros": params["ficheros"]},
        )
        filas = await self._filas(cm_impacto.CONSULTAS["dependencias_ocultas"], params)
        return cm_impacto.construir_ocultas(consulta, [str(f["id"]) for f in semillas], filas)

    # -- Búsqueda, nuggets e incidentes (ya tenían su versión async en core) -----------------
    async def buscar_hibrido(self, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
        return await cm_busqueda.buscar_hibrido(self.db, consulta)

    async def resolver_nuggets(self, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
        return await cm_consulta.resolver_nuggets(self.db, consulta)

    async def resolver_incidentes(self, consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut:
        if consulta.con_area():
            await self._limitar_tiempo()
        return await cm_incidentes.resolver_incidentes(self.db, consulta)
