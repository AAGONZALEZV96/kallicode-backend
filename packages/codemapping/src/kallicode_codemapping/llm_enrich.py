"""Fase 4 - enriquecimiento semántico con DeepSeek-V4.

Principio: lo determinista ya está construido (código, datos). El LLM
solo añade semántica. Toda salida se valida como JSON.

`settings` se recibe siempre por parámetro, nunca de un global: dos jobs de
tenants distintos en el mismo proceso no deben poder mezclar su modelo, su
clave de API ni su umbral de confianza por usar el mismo cliente compartido.
"""

from __future__ import annotations

import json
from typing import Any, cast

from openai import OpenAI

from .config import Settings
from .parser_treesitter import Symbol

# ---- CONSTANTES DE PROMPTS DEL SISTEMA ----
DESCRIBE_SYSTEM = (
    "You annotate code symbols for the CodeMapping knowledge graph of a client system. "
    "For each symbol (name, kind, file, signature, snippet) produce: a one-sentence "
    "semantic description (business terms when possible); its role (domain | infra | glue); "
    "the tables or external APIs it touches ONLY if visible in the snippet - never guess; "
    'and tags. Return ONLY JSON: {"symbols": [{"id", "description_en", "role", '
    '"touches": {"tables": [], "apis": []}, "tags": []}]}'
)

CONSOLIDATE_SYSTEM = (
    "You consolidate described code symbols of ONE module into named technical Definitions "
    "- the middle layer of the knowledge graph. A Definition groups the symbols that together "
    "implement one technical capability (e.g. controller + service + repository of 'invoice creation' "
    "= one Definition). Every symbol id must appear in exactly one definition; use kind 'misc' "
    'for leftovers. Return ONLY JSON: {"definitions": [{"id", "name_en", "kind", '
    '"description_en", "member_symbol_ids": []}]}'
)

ONTOLOGY_SYSTEM = (
    "You induce the business ontology of a client system - the top layer of its knowledge graph. "
    "From consolidated technical definitions and documentation excerpts, derive the 20-60 BUSINESS "
    "concepts the system implements (e.g. User, Trip, Fare, Corporate Agreement). Name each concept "
    "in the client language AND English. Concepts must be business realities, not technical artifacts: "
    "'KafkaConsumer' is not a concept; 'Trip settlement' is. Propose relations between concepts "
    "(has, belongs_to, produces, settles). Mark low-confidence entries: a human curator reviews "
    'this layer before it enters the graph. Return ONLY JSON: {"concepts": [{"name_local", "name_en", '
    '"description_en", "confidence"}], "relations": [{"from", "to", "kind"}]}'
)

LINK_SYSTEM = (
    "You link technical Definitions to business Concepts in a knowledge graph. For each definition, "
    "list the concepts it expresses with a confidence 0..1. Link only what the description supports; "
    "when unsure, lower the confidence - low-confidence links go to a human curation queue instead "
    'of the graph. Return ONLY JSON: {"links": [{"definition_id", "concept_name_en", "confidence"}]}'
)


def _chat(settings: Settings, model: str, system: str, user: str, retries: int = 3) -> dict[str, Any]:
    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    for _ in range(retries):
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content
        if not content:
            continue
        try:
            return cast(dict[str, Any], json.loads(content))
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"LLM sin JSON valido tras {retries} intentos")


def describe_symbols(settings: Settings, symbols: list[Symbol]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(symbols), settings.batch_size):
        batch = symbols[i : i + settings.batch_size]
        user = json.dumps(
            [
                {
                    "id": s.id,
                    "name": s.name,
                    "kind": s.kind,
                    "file": s.file,
                    "signature": s.signature,
                    "snippet": s.snippet,
                }
                for s in batch
            ],
            ensure_ascii=False,
        )
        result = _chat(settings, settings.model_flash, DESCRIBE_SYSTEM, user)
        out.extend(cast(list[dict[str, Any]], result.get("symbols", [])))
    return out


def consolidate_definitions(
    settings: Settings, module: str, described_symbols: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    user = json.dumps({"module": module, "symbols": described_symbols}, ensure_ascii=False)
    result = _chat(settings, settings.model_flash, CONSOLIDATE_SYSTEM, user)
    return cast(list[dict[str, Any]], result.get("definitions", []))


def induce_ontology(
    settings: Settings, definitions: list[dict[str, Any]], docs_excerpts: list[str], language: str = "es"
) -> dict[str, Any]:
    user = json.dumps(
        {
            "client_language": language,
            "definitions": definitions[:800],
            "docs": docs_excerpts[:50],
        },
        ensure_ascii=False,
    )
    return _chat(settings, settings.model_pro, ONTOLOGY_SYSTEM, user)


def link_definitions(
    settings: Settings, definitions: list[dict[str, Any]], concepts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    links: list[dict[str, Any]] = []
    curation_queue: list[dict[str, Any]] = []
    for i in range(0, len(definitions), settings.batch_size):
        batch = definitions[i : i + settings.batch_size]
        user = json.dumps({"definitions": batch, "concepts": concepts}, ensure_ascii=False)
        result = _chat(settings, settings.model_flash, LINK_SYSTEM, user)
        for link in result.get("links", []):
            target = links if link["confidence"] >= settings.link_confidence_threshold else curation_queue
            target.append(link)
    return links, curation_queue
