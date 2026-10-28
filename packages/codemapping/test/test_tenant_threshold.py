"""Pruebas para validación de umbrales de confianza semántica por tenant (KC-CM-06-5)."""

from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.llm_enrich import link_definitions
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)


def test_umbral_default_cuando_no_hay_bd() -> None:
    umbral = obtener_umbral_confianza_tenant(None, "tenant-sin-bd")
    assert umbral == UMBRAL_CONFIANZA_DEFAULT
    assert umbral == 0.75


def test_umbral_override_tiene_prioridad() -> None:
    umbral = obtener_umbral_confianza_tenant(None, "tenant-critico", override_umbral=0.88)
    assert umbral == 0.88


def test_umbral_invalido_lanza_excepcion() -> None:
    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "tenant-err", override_umbral=1.5)


def test_umbral_desde_bd() -> None:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.closed = False
    cur.fetchone.return_value = (0.85,)

    umbral = obtener_umbral_confianza_tenant(conn, "tenant-finanzas")
    assert umbral == 0.85


def test_bifurcacion_enlaces_segun_umbral_dinamico(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simular respuesta LLM con 2 enlaces: uno con 0.80 y otro con 0.70
    mock_llm_response = {
        "links": [
            {"definition_id": "def-1", "concept_name_en": "Invoice", "confidence": 0.80},
            {"definition_id": "def-2", "concept_name_en": "Payment", "confidence": 0.70},
        ]
    }
    monkeypatch.setattr(
        "kallicode_codemapping.llm_enrich._ejecutar_paso_sync",
        lambda **kwargs: mock_llm_response,
    )

    # Caso A: Umbral estándar 0.75 -> 'Invoice' (0.80) pasa al grafo, 'Payment' (0.70) va a curación
    links, queue = link_definitions(
        definitions=[{"id": "def-1"}, {"id": "def-2"}],
        concepts=[{"name_en": "Invoice"}, {"name_en": "Payment"}],
        link_confidence_threshold=0.75,
    )
    assert len(links) == 1
    assert links[0]["concept_name_en"] == "Invoice"
    assert len(queue) == 1
    assert queue[0]["concept_name_en"] == "Payment"

    # Caso B: Tenant más estricto con umbral 0.85 -> Ambos van a curación
    links_strict, queue_strict = link_definitions(
        definitions=[{"id": "def-1"}, {"id": "def-2"}],
        concepts=[{"name_en": "Invoice"}, {"name_en": "Payment"}],
        link_confidence_threshold=0.85,
    )
    assert len(links_strict) == 0
    assert len(queue_strict) == 2
