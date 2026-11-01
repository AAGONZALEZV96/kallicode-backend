"""Pruebas dirigidas de alta sensibilidad para matar mutantes en codemapping (> 90%)."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    estimar_tokens_texto,
    verificar_presupuesto_tenant,
)
from kallicode_codemapping.curation import (
    fabricar_curation_items,
)
from kallicode_codemapping.ontology_mapreduce import (
    agrupar_definiciones_por_modulo,
    inducir_ontologia_map_reduce,
)
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)


# =====================================================================
# 1. MUTANTES EN tenant_thresholds.py
# =====================================================================
def test_mutantes_tenant_threshold_constantes_y_tipos() -> None:
    # Mata mutaciones sobre el valor por defecto
    assert UMBRAL_CONFIANZA_DEFAULT == 0.75
    assert isinstance(UMBRAL_CONFIANZA_DEFAULT, float)

    # Mata mutaciones en validación de límites (<= 0.0, >= 1.0)
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.0) == 0.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0) == 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.5) == 0.5

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=-0.0001)

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0001)


def test_mutantes_tenant_threshold_comportamiento_db() -> None:
    # 1. Conexión None
    assert obtener_umbral_confianza_tenant(None, "t_none") == 0.75

    # 2. Conexión cerrada
    conn_closed = MagicMock()
    conn_closed.closed = True
    assert obtener_umbral_confianza_tenant(conn_closed, "t_closed") == 0.75

    # 3. Excepción en consulta
    conn_err = MagicMock()
    conn_err.closed = False
    conn_err.cursor.side_effect = RuntimeError("DB timeout")
    assert obtener_umbral_confianza_tenant(conn_err, "t_err") == 0.75

    # 4. Fila no encontrada
    conn_vacia = MagicMock()
    conn_vacia.closed = False
    cur_vacia = MagicMock()
    conn_vacia.cursor.return_value.__enter__.return_value = cur_vacia
    cur_vacia.fetchone.return_value = None
    assert obtener_umbral_confianza_tenant(conn_vacia, "t_vacia") == 0.75

    # 5. Valor devuelto None
    cur_vacia.fetchone.return_value = (None,)
    assert obtener_umbral_confianza_tenant(conn_vacia, "t_none_val") == 0.75

    # 6. Valor fuera de rango en base de datos
    cur_vacia.fetchone.return_value = (1.5,)
    assert obtener_umbral_confianza_tenant(conn_vacia, "t_out_of_bounds") == 0.75

    # 7. Valor válido en base de datos
    cur_vacia.fetchone.return_value = (0.82,)
    assert obtener_umbral_confianza_tenant(conn_vacia, "t_ok") == 0.82


# =====================================================================
# 2. MUTANTES EN budget.py
# =====================================================================
def test_mutantes_budget_estimar_tokens_texto_aritmética() -> None:
    # Verifica aritmética exacta: math.ceil(len(texto) / 3.5)
    assert estimar_tokens_texto("") == 0
    assert estimar_tokens_texto("a") == 1
    assert estimar_tokens_texto("1234") == math.ceil(4 / 3.5)  # 2
    assert estimar_tokens_texto("1234567") == 2  # 7 / 3.5 = 2.0
    assert estimar_tokens_texto("12345678") == 3  # 8 / 3.5 = 2.28 -> 3


def test_mutantes_budget_ingesta_pesos_y_factores() -> None:
    # Caso lista vacía
    p, c = estimar_tokens_ingesta([])
    assert p == 0
    assert c == 0

    # Caso con un símbolo con datos completos
    # Overhead plantilla (~120 tokens) + firma + snippet [:1000]
    simbolo = {
        "signature": "def foo(a: int) -> int:",  # 24 chars -> 7 tokens
        "snippet": "return a + 1\n" * 100,  # > 1000 chars, cortado a 1000 -> 286 tokens
    }
    p1, c1 = estimar_tokens_ingesta([simbolo])
    # 120 + 7 + ceil(1000/3.5) = 120 + 7 + 286 = 413 tokens prompt
    assert p1 == 413
    assert c1 == 150  # 150 tokens fijos por completion

    # Margen de seguridad del 15% (factor 1.15)
    # 413 * 1.15 = 474.95 -> 475
    # 150 * 1.15 = 172.5 -> 173


def test_mutantes_budget_verificar_presupuesto_ramas() -> None:
    simbolos = [{"signature": "def a():", "snippet": "pass"}]

    # 1. Sin cuota configurada (infinito)
    conn_sin_cuota = MagicMock()
    cur_sin_cuota = MagicMock()
    conn_sin_cuota.cursor.return_value.__enter__.return_value = cur_sin_cuota
    cur_sin_cuota.fetchone.return_value = (None, 0)
    verificar_presupuesto_tenant(conn_sin_cuota, "t1", simbolos)

    # 2. Con cuota disponible suficiente
    cur_sin_cuota.fetchone.return_value = (1_000_000, 100)
    verificar_presupuesto_tenant(conn_sin_cuota, "t1", simbolos)

    # 3. Presupuesto excedido
    cur_sin_cuota.fetchone.return_value = (100, 99)
    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn_sin_cuota, "t_excedido", simbolos)
    assert "Presupuesto insuficiente" in str(exc_info.value)


# =====================================================================
# 3. MUTANTES EN curation.py
# =====================================================================
def test_mutantes_curation_creacion_y_campos_completos() -> None:
    raw_links = [
        {
            "definition_id": "def_orig",
            "concept_name_en": "InvoiceEntity",
            "confidence": 0.65,
            "reasoning": "Duda en cardinalidad",
        }
    ]
    items = fabricar_curation_items(
        raw_links=raw_links,
        tenant_id="tenant-alpha",
        snapshot_id="snap-beta",
    )
    assert len(items) == 1
    it = items[0]

    assert it.id.startswith("cur_")
    assert it.tenant_id == "tenant-alpha"
    assert it.snapshot_id == "snap-beta"
    assert it.tipo == "link"
    assert it.origen_id == "def_orig"
    assert it.destino_id == "InvoiceEntity"
    assert it.confianza == 0.65
    assert it.estado == "pendiente"
    assert it.evidencia["motivo"] == "Duda en cardinalidad"

    # Tupla serializada para inserción PostgreSQL
    tup = it.to_tuple()
    assert len(tup) == 10
    assert tup[0] == it.id
    assert tup[1] == "tenant-alpha"
    assert tup[2] == "snap-beta"
    assert tup[3] == "link"
    assert tup[4] == "def_orig"
    assert tup[5] == "InvoiceEntity"
    assert tup[6] == 0.65
    assert tup[8] == "pendiente"


def test_mutantes_curation_fallbacks_defensivos() -> None:
    # Comprobar fallbacks de llaves faltantes o en None
    raw_links = [
        {
            "symbol_id": "sym_fallback",
            "concept_id": "cid_fallback",
            "confidence": None,
            "reasoning": None,
        }
    ]
    items = fabricar_curation_items(raw_links, "t1", "s1")
    assert len(items) == 1
    it = items[0]
    assert it.origen_id == "sym_fallback"
    assert it.destino_id == "cid_fallback"
    assert it.confianza == 0.0
    assert it.evidencia["motivo"] == "Confianza inferior al umbral del tenant"


# =====================================================================
# 4. MUTANTES EN ontology_mapreduce.py
# =====================================================================
def test_mutantes_ontology_agrupar_modulos_exhaustivo() -> None:
    defs = [
        {"id": "1", "module": "core"},
        {"id": "2", "module": "core"},
        {"id": "3", "module": "auth"},
        {"id": "4", "module": ""},
        {"id": "5", "module": None},
        {"id": "6"},  # sin clave module
    ]
    grupos = agrupar_definiciones_por_modulo(defs)
    assert len(grupos["core"]) == 2
    assert len(grupos["auth"]) == 1
    assert len(grupos["root"]) == 3
    assert {d["id"] for d in grupos["root"]} == {"4", "5", "6"}


def test_mutantes_ontology_mapreduce_algoritmo_completo() -> None:
    # 1. Lista vacía retorna directo
    assert inducir_ontologia_map_reduce([], lambda *args: {}) == {"concepts": []}

    llamadas: list[str] = []

    def mock_llm(paso: str, prompt: str, vars_: dict[str, Any]) -> dict[str, Any]:
        llamadas.append(paso)
        if paso == "codemapping.ontology_map":
            return {"concepts": [{"name_en": f"C_{vars_['modulo']}"}]}
        elif paso == "codemapping.ontology_reduce":
            return {"concepts": [{"name_en": "MergedConcept"}]}
        elif paso == "codemapping.ontology_global":
            return {"concepts": [{"name_en": "FinalCanonical"}]}
        return {}

    defs = [
        {"id": "1", "module": "m1"},
        {"id": "2", "module": "m2"},
    ]

    res = inducir_ontologia_map_reduce(
        definitions=defs,
        ejecutar_llm=mock_llm,
        lote_maximo_defs=1,
        lote_maximo_conceptos=1,
    )

    assert "codemapping.ontology_map" in llamadas
    assert "codemapping.ontology_reduce" in llamadas
    assert "codemapping.ontology_global" in llamadas
    assert res == {"concepts": [{"name_en": "FinalCanonical"}]}
