"""Pruebas para KC-CM-05-3: Adopción del cliente LLM canónico en CodeMapping (CM-F06)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kallicode_codemapping.llm_enrich import (
    consolidate_definitions,
    describe_symbols,
    induce_ontology,
    link_definitions,
)
from kallicode_codemapping.parser_treesitter import Symbol
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.llm.tiers import Tier


def _resultado(salida: dict[str, Any]) -> ResultadoPaso:
    """ResultadoPaso como el que devuelve kallicode_core.llm.ejecutar_paso con esa salida."""
    return ResultadoPaso(
        paso_id="codemapping.test",
        version="test",
        tier=Tier.FLASH,
        modelo="test",
        salida=salida,
        confianza=None,
        validacion="esquema_valido",
        tokens_in=0,
        tokens_out=0,
        duracion_ms=0,
    )


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
    mock_ejecutar.return_value = _resultado(
        {
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
    )

    resultado = describe_symbols(simbolos_dummy, batch_size=2, tenant_id="tenant-prueba")

    assert len(resultado) == 1
    assert resultado[0]["id"] == "sym_1"

    # Verificar contrato de invocación canónico
    mock_ejecutar.assert_called_once()
    kwargs = mock_ejecutar.call_args.kwargs
    assert kwargs["paso_id"] == "codemapping.describe_symbols"
    assert kwargs["tier_minimo"] == Tier.FLASH
    assert kwargs["tenant_id"] == "tenant-prueba"


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_consolidate_definitions_usa_cliente_canonico(mock_ejecutar: AsyncMock) -> None:
    mock_ejecutar.return_value = _resultado(
        {
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
    )

    resultado = consolidate_definitions("src", [{"id": "sym_1"}], tenant_id="tenant-prueba")

    assert len(resultado) == 1
    assert resultado[0]["name_en"] == "Billing Management"
    assert mock_ejecutar.call_args.kwargs["tier_minimo"] == Tier.FLASH


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_induce_ontology_usa_tier_pro(mock_ejecutar: AsyncMock) -> None:
    mock_ejecutar.return_value = _resultado(
        {
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
    )

    defs_simuladas = [
        {
            "id": "def_factura",
            "name_en": "InvoiceService",
            "description_en": "Handles invoice processing",
            "module": "billing",
            "role": "domain",
        }
    ]

    resultado = induce_ontology(
        definitions=defs_simuladas,
        docs_excerpts=[],
        tenant_id="tenant-prueba",
    )

    assert "concepts" in resultado
    assert len(resultado["concepts"]) == 1
    # Ontología debe requerir razonamiento de mayor capacidad (Tier PRO)
    assert mock_ejecutar.call_args.kwargs["tier_minimo"] == Tier.PRO


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_link_definitions_filtra_cola_curacion_por_umbral(mock_ejecutar: AsyncMock) -> None:
    # Simular un enlace con alta confianza (pasa al grafo) y otro con baja confianza (curación)
    mock_ejecutar.return_value = _resultado(
        {
            "links": [
                {"definition_id": "def_1", "concept_name_en": "Invoice", "confidence": 0.85},
                {"definition_id": "def_2", "concept_name_en": "AuditLog", "confidence": 0.40},
            ]
        }
    )

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


@patch("kallicode_codemapping.llm_enrich.ejecutar_paso", new_callable=AsyncMock)
def test_link_definitions_acepta_conn_y_usa_el_umbral_del_tenant(mock_ejecutar: AsyncMock) -> None:
    """Regresión: build_graph pasaba conn= y link_definitions no lo aceptaba (TypeError)."""
    mock_ejecutar.return_value = _resultado(
        {"links": [{"definition_id": "def_1", "concept_name_en": "Invoice", "confidence": 0.80}]}
    )
    conn = MagicMock(closed=False)
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (0.85,)

    links, cola = link_definitions([{"id": "def_1"}], [], tenant_id="tenant-estricto", conn=conn)

    assert links == []
    assert [c["definition_id"] for c in cola] == ["def_1"]
    _sql, params = conn.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params[1] == "tenant-estricto"
    assert mock_ejecutar.call_args.kwargs["paso_id"] == "codemapping.link_definitions"
    assert mock_ejecutar.call_args.kwargs["entrada"]["definitions"] == [{"id": "def_1"}]
