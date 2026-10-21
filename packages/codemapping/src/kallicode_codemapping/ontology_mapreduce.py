"""Módulo de inducción jerárquica de ontología mediante Map-Reduce (KC-CM-07-1).

Garantiza la síntesis completa de conceptos de negocio sin truncamiento de contexto
en repositorios de gran escala.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .security import (
    INSTRUCCION_DATOS_NO_CONFIABLES,
    construir_payload_seguro_definicion,
    generar_delimitador,
    validar_conceptos_salida,
)

logger = logging.getLogger(__name__)

# Prompt del reductor de conceptos para fusionar ontologías modulares
ONTOLOGY_REDUCE_SYSTEM = (
    "You are a Principal Software Architect synthesizing a canonical business ontology. "
    "You receive local domain concepts induced from different modules of a software system. "
    "Merge duplicate or synonymous concepts, reconcile conflicting definitions, "
    "and produce a unified global business ontology. "
    "Return ONLY valid JSON strictly matching the schema: "
    '{"concepts": [{"name_en": str, "description_en": str, "category": str, "synonyms": list[str]}]}'
    + INSTRUCCION_DATOS_NO_CONFIABLES
)

ONTOLOGY_MODULE_MAP_SYSTEM = (
    "You are a Domain-Driven Design (DDD) specialist. "
    "Analyze the technical definitions of a specific software module and induce its core "
    "business domain concepts. "
    "Return ONLY valid JSON strictly matching the schema: "
    '{"concepts": [{"name_en": str, "description_en": str, "category": str, "synonyms": list[str]}]}'
    + INSTRUCCION_DATOS_NO_CONFIABLES
)


def agrupar_definiciones_por_modulo(
    definitions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Agrupa las definiciones técnicas por módulo de origen."""
    modulos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in definitions:
        modulo = str(d.get("module") or "root")
        modulos[modulo].append(d)
    return dict(modulos)


def inducir_ontologia_map_reduce(
    definitions: list[dict[str, Any]],
    ejecutar_llm: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    lote_maximo_defs: int = 150,
    lote_maximo_conceptos: int = 50,
    rechazados: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Ejecuta el pipeline jerárquico Map-Reduce para inducir la ontología de negocio.

    Fases:
    1. Map: Inducción de conceptos locales por módulo (dividido en sub-lotes si excede lote_maximo_defs).
    2. Reduce: Consolidación jerárquica y deduplicación en la ontología global.

    Anti-injection (KC-CM-07-4): el texto que viene del código se sanea y se delimita con un nonce
    por lote, y toda respuesta del LLM se valida contra el esquema antes de pasar a la fase
    siguiente. Los conceptos descartados se registran y, si se pasa `rechazados`, se devuelven.
    """
    descartados: list[dict[str, str]] = rechazados if rechazados is not None else []

    if not definitions:
        return {"concepts": []}

    # -------------------------------------------------------------
    # 1. MAP: Conceptos locales por módulo sin truncar
    # -------------------------------------------------------------
    por_modulo = agrupar_definiciones_por_modulo(definitions)
    conceptos_locales: list[dict[str, Any]] = []

    for mod_name, defs_modulo in por_modulo.items():
        for i in range(0, len(defs_modulo), lote_maximo_defs):
            sub_lote = defs_modulo[i : i + lote_maximo_defs]
            delimitador = generar_delimitador()
            payload_map = {
                "module": mod_name,
                "delimiter": delimitador,
                "definitions": [
                    construir_payload_seguro_definicion(d, delimitador) for d in sub_lote
                ],
            }
            res_map = ejecutar_llm(
                "codemapping.ontology_map",
                ONTOLOGY_MODULE_MAP_SYSTEM,
                payload_map,
            )
            validos, malos = validar_conceptos_salida(res_map)
            descartados.extend(malos)
            conceptos_locales.extend(validos)
    # -------------------------------------------------------------
    # 2. REDUCE: Fusión y síntesis transversal sin exceder contexto
    # -------------------------------------------------------------
    if not conceptos_locales:
        return {"concepts": []}

    conceptos_acumulados = list(conceptos_locales)

    # Reducción jerárquica iterativa si la cantidad de conceptos locales es muy extensa
    while len(conceptos_acumulados) > lote_maximo_conceptos:
        nuevos_reducidos: list[dict[str, Any]] = []
        for i in range(0, len(conceptos_acumulados), lote_maximo_conceptos):
            sub_lote_conceptos = conceptos_acumulados[i : i + lote_maximo_conceptos]
            payload_reduce = {"local_concepts": sub_lote_conceptos}
            res_reduce = ejecutar_llm(
                "codemapping.ontology_reduce",
                ONTOLOGY_REDUCE_SYSTEM,
                payload_reduce,
            )
            validos, malos = validar_conceptos_salida(res_reduce)
            descartados.extend(malos)
            nuevos_reducidos.extend(validos)
        conceptos_acumulados = nuevos_reducidos

    # Reducción final canónica
    payload_final = {"local_concepts": conceptos_acumulados}
    res_final = ejecutar_llm(
        "codemapping.ontology_global",
        ONTOLOGY_REDUCE_SYSTEM,
        payload_final,
    )

    finales, malos = validar_conceptos_salida(res_final)
    descartados.extend(malos)
    if descartados:
        logger.warning(
            "ontologia: %d concepto(s) descartado(s) por esquema o posible inyección",
            len(descartados),
        )
    # Si el reductor final no devolvió nada válido, se conserva lo ya validado de la fase anterior.
    return {"concepts": finales or conceptos_acumulados}
