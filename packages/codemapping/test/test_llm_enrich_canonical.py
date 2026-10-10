"""Pruebas para KC-CM-05-3: Adopción del cliente LLM canónico en CodeMapping (CM-F06)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from kallicode_codemapping.llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from kallicode_codemapping.parser_treesitter import Symbol
from kallicode_core.llm.tiers import Tier


@pytest.fixture
def simbolos_dummy() -> list[Symbol]:
    return [
        Symbol(
            id="sym_1",
            name="crear_factura",
            kind="function",
            file="src/billing.py",
            language="python",
            start_line=1,
            end_line=2,
            signature="def crear_factura(monto: float) -> str:",
            snippet="def crear_factura(monto: float) -> str:\n    return 'FAC-001'",
        ),
        Symbol(
            id="sym_2",
            name="FacturaRepository",
            kind="class",
            file="src/billing.py",
            language="python",
            start_line=5,
            end_line=6,
            signature="class FacturaRepository:",
            snippet="class FacturaRepository:\n    pass",
        ),
    ]


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_describe_symbols_usa_cliente_canonico_flash(
    mock_ejecutar: AsyncMock, simbolos_dummy: list[Symbol]
) -> None:
    mock_ejecutar.return_value = {
        "symbols": [
            {
                "id": "sym_1",
                "description_en": "Creates a financial invoice.",
                "role": "domain",
                "touches": {"tables": ["invoices"], "apis": []},
                "tags": ["billing"],
            }
        ]
    }

    resultado = describe_symbols(simbolos_dummy, batch_size=2, tenant_id="tenant-prueba")

    assert len(resultado) == 1
    assert resultado[0]["id"] == "sym_1"

    # Verificar contrato de invocación canónico
    mock_ejecutar.assert_called_once()
    kwargs = mock_ejecutar.call_args.kwargs
    assert kwargs["nombre_paso"] == "codemapping.describe_symbols"
    assert kwargs["tier_inicial"] == Tier.FLASH
    assert kwargs["tenant_id"] == "tenant-prueba"
    assert kwargs["escalar_automaticamente"] is True


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_consolidate_definitions_usa_cliente_canonico(mock_ejecutar: AsyncMock) -> None:
    mock_ejecutar.return_value = {
        "definitions": [
            {
                "id": "def_billing",
                "name_en": "Billing Management",
                "kind": "domain",
                "description_en": "Handles invoice lifecycle.",
                "member_symbol_ids": ["sym_1", "sym_2"],
            }
        ]
    }

    resultado = consolidate_definitions("src", [{"id": "sym_1"}], tenant_id="tenant-prueba")

    assert len(resultado) == 1
    assert resultado[0]["name_en"] == "Billing Management"
    assert mock_ejecutar.call_args.kwargs["tier_inicial"] == Tier.FLASH


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_induce_ontology_usa_tier_pro(mock_ejecutar: AsyncMock) -> None:
    mock_ejecutar.return_value = {
        "concepts": [
            {
                "name_local": "Factura",
                "name_en": "Invoice",
                "description_en": "Commercial invoice reality.",
                "confidence": 0.95,
            }
        ],
        "relations": [],
    }

    resultado = induce_ontology(definitions=[], docs_excerpts=[], tenant_id="tenant-prueba")

    assert "concepts" in resultado
    assert len(resultado["concepts"]) == 1
    # Ontología debe requerir razonamiento de mayor capacidad (Tier PRO)
    assert mock_ejecutar.call_args.kwargs["tier_inicial"] == Tier.PRO


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_link_definitions_filtra_cola_curacion_por_umbral(mock_ejecutar: AsyncMock) -> None:
    # Simular un enlace con alta confianza (pasa al grafo) y otro con baja confianza (curación)
    mock_ejecutar.return_value = {
        "links": [
            {"definition_id": "def_1", "concept_name_en": "Invoice", "confidence": 0.85},
            {"definition_id": "def_2", "concept_name_en": "AuditLog", "confidence": 0.40},
        ]
    }

    defs = [{"id": "def_1"}, {"id": "def_2"}]
    concepts = [{"name_en": "Invoice"}, {"name_en": "AuditLog"}]

    links_aprobados, cola_curacion = link_definitions(
        defs, concepts, batch_size=2, tenant_id="tenant-prueba"
    )

    assert len(links_aprobados) == 1
    assert links_aprobados[0]["definition_id"] == "def_1"

    assert len(cola_curacion) == 1
    assert cola_curacion[0]["definition_id"] == "def_2"
    assert cola_curacion[0]["confidence"] == 0.40
