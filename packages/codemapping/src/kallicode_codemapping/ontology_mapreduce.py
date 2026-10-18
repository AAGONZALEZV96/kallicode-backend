"""Módulo de inducción jerárquica de ontología mediante Map-Reduce (KC-CM-07-1).

Garantiza la síntesis completa de conceptos de negocio sin truncamiento de contexto
en repositorios de gran escala.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

# Prompt del reductor de conceptos para fusionar ontologías modulares
ONTOLOGY_REDUCE_SYSTEM = (
    "You are a Principal Software Architect synthesizing a canonical business ontology. "
    "You receive local domain concepts induced from different modules of a software system. "
    "Merge duplicate or synonymous concepts, reconcile conflicting definitions, "
    "and produce a unified global business ontology. "
    "Return ONLY valid JSON strictly matching the schema: "
    '{"concepts": [{"name_en": str, "description_en": str, "category": str, "synonyms": list[str]}]}'
)

ONTOLOGY_MODULE_MAP_SYSTEM = (
    "You are a Domain-Driven Design (DDD) specialist. "
    "Analyze the technical definitions of a specific software module and induce its core "
    "business domain concepts. "
    "Return ONLY valid JSON strictly matching the schema: "
    '{"concepts": [{"name_en": str, "description_en": str, "category": str, "synonyms": list[str]}]}'
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
) -> dict[str, Any]:
    """Ejecuta el pipeline jerárquico Map-Reduce para inducir la ontología de negocio.

    Fases:
    1. Map: Inducción de conceptos locales por módulo (dividido en sub-lotes si excede lote_maximo_defs).
    2. Reduce: Consolidación jerárquica y deduplicación en la ontología global.
    """
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
            payload_map = {
                "module": mod_name,
                "definitions": [
                    {
                        "id": d.get("id"),
                        "name_en": d.get("name_en"),
                        "description_en": d.get("description_en"),
                        "role": d.get("role"),
                    }
                    for d in sub_lote
                ],
            }
            res_map = ejecutar_llm(
                "codemapping.ontology_map",
                ONTOLOGY_MODULE_MAP_SYSTEM,
                payload_map,
            )
            conceptos_locales.extend(res_map.get("concepts", []))
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
            nuevos_reducidos.extend(res_reduce.get("concepts", []))
        conceptos_acumulados = nuevos_reducidos

    # Reducción final canónica
    payload_final = {"local_concepts": conceptos_acumulados}
    res_final = ejecutar_llm(
        "codemapping.ontology_global",
        ONTOLOGY_REDUCE_SYSTEM,
        payload_final,
    )

    return {"concepts": res_final.get("concepts", conceptos_acumulados)}
