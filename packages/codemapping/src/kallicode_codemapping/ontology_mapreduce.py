"""Módulo de inducción jerárquica de ontología mediante Map-Reduce (KC-CM-07-1).

Garantiza la síntesis completa de conceptos de negocio sin truncamiento de contexto
en repositorios de gran escala.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from kallicode_core.llm.plantillas import registrar_plantilla
from kallicode_core.llm.tiers import Tier

from .security import (
    INSTRUCCION_DATOS_NO_CONFIABLES,
    INSTRUCCION_DOCS_NO_CONFIABLES,
    construir_payload_seguro_definicion,
    construir_payload_seguro_extracto_doc,
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

# Prompt del mapeador de documentación (KC-CM-08-1): manuales, specs, ADRs y documentos de negocio
# del cliente aportan vocabulario de dominio que el código no nombra (reglas, actores, procesos).
ONTOLOGY_DOCS_MAP_SYSTEM = (
    "You are a Domain-Driven Design (DDD) specialist. "
    "Analyze excerpts of the customer's technical and business documentation (manuals, "
    "specifications, architecture decision records, business documents) and induce the core "
    "business domain concepts they describe. "
    "Return ONLY valid JSON strictly matching the schema: "
    '{"concepts": [{"name_en": str, "description_en": str, "category": str, "synonyms": list[str]}]}'
    + INSTRUCCION_DOCS_NO_CONFIABLES
)

# Esquema común de las fases Map y Reduce (lo que exige validar_conceptos_salida, en estricto).
ESQUEMA_CONCEPTOS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["concepts"],
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name_en", "description_en"],
                "properties": {
                    "name_en": {"type": "string"},
                    "description_en": {"type": "string"},
                    "category": {"type": "string"},
                    "synonyms": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

for _paso, _system, _descripcion, _entrada in (
    (
        "codemapping.ontology_map",
        ONTOLOGY_MODULE_MAP_SYSTEM,
        "Map: conceptos de negocio locales de un módulo a partir de sus definiciones.",
        {"module": "str", "delimiter": "nonce", "definitions": "list[definición delimitada]"},
    ),
    (
        "codemapping.ontology_docs_map",
        ONTOLOGY_DOCS_MAP_SYSTEM,
        "Map: conceptos de negocio a partir de extractos de la documentación del cliente.",
        {"delimiter": "nonce", "documents": "list[{excerpt}] delimitados"},
    ),
    (
        "codemapping.ontology_reduce",
        ONTOLOGY_REDUCE_SYSTEM,
        "Reduce intermedio: fusiona sub-lotes de conceptos locales.",
        {"local_concepts": "list[concepto]"},
    ),
    (
        "codemapping.ontology_global",
        ONTOLOGY_REDUCE_SYSTEM,
        "Reduce final: ontología global canónica del tenant.",
        {"local_concepts": "list[concepto]"},
    ),
):
    registrar_plantilla(
        _paso,
        tier=Tier.PRO,
        escalable=True,
        descripcion=_descripcion,
        entrada=_entrada,
        salida=ESQUEMA_CONCEPTOS,
        system=_system,
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
    docs_excerpts: list[str] | None = None,
    lote_maximo_docs: int = 20,
) -> dict[str, Any]:
    """Ejecuta el pipeline jerárquico Map-Reduce para inducir la ontología de negocio.

    Fases:
    1. Map: Inducción de conceptos locales por módulo (dividido en sub-lotes si excede lote_maximo_defs).
    1b. Map de documentación (KC-CM-08-1): conceptos inducidos de los extractos de docs del cliente,
        en sub-lotes de `lote_maximo_docs`. Entran a la misma fase Reduce que los del código, así
        que un concepto que aparece en ambos se fusiona en vez de duplicarse.
    2. Reduce: Consolidación jerárquica y deduplicación en la ontología global.

    Anti-injection (KC-CM-07-4): el texto que viene del código y de la documentación se sanea y se
    delimita con un nonce por lote, y toda respuesta del LLM se valida contra el esquema antes de
    pasar a la fase siguiente. Los conceptos descartados se registran y, si se pasa `rechazados`,
    se devuelven.
    """
    descartados: list[dict[str, str]] = rechazados if rechazados is not None else []
    extractos = [e for e in (docs_excerpts or []) if e and e.strip()]

    if not definitions and not extractos:
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
    # 1b. MAP de documentación: extractos delimitados como dato no confiable
    # -------------------------------------------------------------
    for i in range(0, len(extractos), lote_maximo_docs):
        sub_lote_docs = extractos[i : i + lote_maximo_docs]
        delimitador = generar_delimitador()
        payload_docs = {
            "delimiter": delimitador,
            "documents": [
                construir_payload_seguro_extracto_doc(e, delimitador) for e in sub_lote_docs
            ],
        }
        res_docs = ejecutar_llm(
            "codemapping.ontology_docs_map",
            ONTOLOGY_DOCS_MAP_SYSTEM,
            payload_docs,
        )
        validos, malos = validar_conceptos_salida(res_docs)
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
