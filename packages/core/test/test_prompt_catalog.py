"""Test de contrato: verifica la integridad y tipado del catálogo de plantillas LLM."""

import pytest
from  kallicode_core.llm.plantillas import PLANTILLAS, plantilla


def test_plantillas_catalog_integrity() -> None:
    """Valida que cada plantilla tenga los campos requeridos por el contrato."""
    assert len(PLANTILLAS) >= 20, "El catálogo debe contener las plantillas base del pipeline"

    required_keys = {"version", "tier", "escalable", "descripcion", "entrada", "salida", "system"}

    for prompt_id, spec in PLANTILLAS.items():
        missing = required_keys - set(spec.keys())
        assert not missing, f"Plantilla '{prompt_id}' no define: {missing}"

        # Validar salida JSON Schema
        salida = spec["salida"]
        assert isinstance(salida, dict), f"Salida de '{prompt_id}' debe ser un dict JSON Schema"
        assert "type" in salida, f"Salida de '{prompt_id}' debe especificar 'type'"

        # Validar prompt de sistema en inglés no vacío
        system = spec["system"]
        assert isinstance(system, str) and len(system) > 20, f"Prompt system en '{prompt_id}' inválido"


def test_plantilla_helper_lookup() -> None:
    """Prueba la función de consulta rápida."""
    triage = plantilla("triage.classify")
    assert triage["version"] == "v1"

    with pytest.raises(KeyError):
        plantilla("paso.inexistente")