"""Módulo de Pipeline y Máquina de Estados de KalliCode."""

from .estados import (
    TRANSICIONES_VALIDAS,
    EstadoJob,
    es_transicion_valida,
    obtener_destinos_posibles,
)
from .transiciones import transicionar

__all__: list[str] = [
    "TRANSICIONES_VALIDAS",
    "EstadoJob",
    "es_transicion_valida",
    "obtener_destinos_posibles",
    "transicionar",
]