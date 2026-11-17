"""Capa de consulta de CodeMapping por HTTP para los agentes (KC-CM-11-5).

Expone las operaciones del contrato cm-grafo-v1 (`kallicode_core.cm_grafo.OPERACIONES`) sobre la
sesión RLS del tenant del token de servicio. Son lecturas: la Idempotency-Key que exige la API
interna no se registra. Los modelos de entrada y salida son los de `kallicode_core`; aquí no se
define ninguno. Una caída de PostgreSQL la traduce a 503 DEPENDENCIA_NO_DISPONIBLE el manejador
global de main.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from kallicode_core.cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut
from kallicode_core.cm_grafo_sesion import ConsultaGrafoSesion
from kallicode_core.cm_impacto import (
    TIEMPO_MAXIMO_MS,
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

from ..deps import ServicioActual, servicio_actual, sesion_de

PREFIJO = "/codemapping/grafo"

router = APIRouter(prefix=PREFIJO, tags=["Consulta CodeMapping"])


def _sesion(svc: ServicioActual):
    """Sesión RLS con el statement_timeout del grafo fijado en el mismo viaje que el tenant
    (KC-CM-11-6): acota las diez operaciones, no solo los recorridos."""
    return sesion_de(svc, statement_timeout_ms=TIEMPO_MAXIMO_MS)


# Operación del contrato -> ruta HTTP. test_codemapping_grafo.py comprueba que cubre OPERACIONES.
RUTAS_OPERACIONES: dict[str, str] = {
    "resolver_nodos": "/resolver-nodos",
    "semillas": "/semillas",
    "impacto": "/impacto",
    "dependencias": "/dependencias",
    "subgrafo": "/subgrafo",
    "vecinos": "/vecinos",
    "dependencias_ocultas": "/dependencias-ocultas",
    "buscar_hibrido": "/busqueda",
    "resolver_nuggets": "/nuggets",
    "resolver_incidentes": "/incidentes",
}


@router.post(RUTAS_OPERACIONES["resolver_nodos"], summary="Resolver ids de nodos del grafo")
async def resolver_nodos(
    consulta: ResolverNodosIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> list[NodoGrafo]:
    """Ids que existen en el grafo del tenant, con su kind."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).resolver_nodos(consulta)


@router.post(RUTAS_OPERACIONES["semillas"], summary="Nodos de partida de un área")
async def semillas(
    consulta: ConsultaGrafoIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> list[NodoGrafo]:
    """Los nodos pedidos y cada fichero con sus símbolos."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).semillas(consulta)


@router.post(RUTAS_OPERACIONES["impacto"], summary="Impacto a k saltos")
async def impacto(
    consulta: ConsultaGrafoIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> RecorridoOut:
    """Quién depende del área hasta k saltos (aristas en sentido inverso)."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).impacto(consulta)


@router.post(RUTAS_OPERACIONES["dependencias"], summary="Dependencias a k saltos")
async def dependencias(
    consulta: ConsultaGrafoIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> RecorridoOut:
    """De qué depende el área hasta k saltos (aristas hacia delante)."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).dependencias(consulta)


@router.post(RUTAS_OPERACIONES["subgrafo"], summary="Subgrafo a k saltos")
async def subgrafo(
    consulta: ConsultaGrafoIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> SubgrafoOut:
    """Vecindad del área en los dos sentidos hasta k saltos y las aristas entre sus nodos."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).subgrafo(consulta)


@router.post(RUTAS_OPERACIONES["vecinos"], summary="Vecinos a un salto")
async def vecinos(
    consulta: VecinosIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> list[Arista]:
    """Aristas a un salto de un nodo, por sentido y tipo."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).vecinos(consulta)


@router.post(RUTAS_OPERACIONES["dependencias_ocultas"], summary="Dependencias ocultas")
async def dependencias_ocultas(
    consulta: ConsultaGrafoIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> DependenciasOcultasOut:
    """Nodos acoplados por una tabla compartida que no se alcanzan por CALLS."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).dependencias_ocultas(consulta)


@router.post(RUTAS_OPERACIONES["buscar_hibrido"], summary="Búsqueda semántica híbrida")
async def buscar_hibrido(
    consulta: BusquedaHibridaIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> BusquedaHibridaOut:
    """Los k elementos más parecidos a un embedding BGE-M3 en las capas pedidas."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).buscar_hibrido(consulta)


@router.post(RUTAS_OPERACIONES["resolver_nuggets"], summary="Resolver {{nuggets}}")
async def resolver_nuggets(
    consulta: ConsultaNuggetsIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> ConsultaNuggetsOut:
    """Reglas aprobadas aplicables a un área, recortadas al presupuesto."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).resolver_nuggets(consulta)


@router.post(RUTAS_OPERACIONES["resolver_incidentes"], summary="Resolver {{incidents}}")
async def resolver_incidentes(
    consulta: ConsultaIncidentesIn,
    svc: ServicioActual = Depends(servicio_actual),
) -> ConsultaIncidentesOut:
    """Incidentes del tenant enlazados a nodos cercanos al área, recortados al presupuesto."""
    async with _sesion(svc) as db:
        return await ConsultaGrafoSesion(db, tiempo_limitado=True).resolver_incidentes(consulta)
