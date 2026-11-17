"""Módulo de Pipeline y Máquina de Estados de KalliCode."""

from .estados import (
    TRANSICIONES_VALIDAS,
    EstadoJob,
    es_transicion_valida,
    obtener_destinos_posibles,
)
from .transiciones import transicionar
from .vista import (
    ETAPA_DE_GATE,
    ETAPAS_FLUJO,
    EstadoPaso,
    EtapaFlujo,
    EtapaTicket,
    PasoPipeline,
    etapa_visible,
    mapear_pipeline,
)

__all__: list[str] = [
    "ETAPAS_FLUJO",
    "ETAPA_DE_GATE",
    "TRANSICIONES_VALIDAS",
    "EstadoJob",
    "EstadoPaso",
    "EtapaFlujo",
    "EtapaTicket",
    "PasoPipeline",
    "es_transicion_valida",
    "etapa_visible",
    "mapear_pipeline",
    "obtener_destinos_posibles",
    "transicionar",
]
