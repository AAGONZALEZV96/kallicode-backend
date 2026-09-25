"""Pruebas de endurecimiento para KC-FAB-03-2 (additionalProperties y version_hash)."""

from typing import Any

import pytest
from kallicode_core.llm.plantillas import PLANTILLAS


def test_catalogo_contiene_exactamente_21_plantillas() -> None:
    """Verifica que el inventario registre el catálogo canónico del pipeline."""
    # 21 plantillas operativas de las 6 etapas (Triage..Deploy) + 1 de orquestación
    assert len(PLANTILLAS) in (21, 22)

@pytest.mark.parametrize("nombre", list(PLANTILLAS.keys()))
def test_plantillas_bloquean_additional_properties_recursivo(nombre: str) -> None:
    """Valida que todos los esquemas de objeto prohíban propiedades adicionales."""
    p = PLANTILLAS[nombre]
    schema = p["salida"]

    def _verificar_esquema(s: dict[str, Any]) -> None:
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False, f"Plantilla {nombre} permite additionalProperties en: {s}"
            for prop in s.get("properties", {}).values():
                if isinstance(prop, dict):
                    _verificar_esquema(prop)
        elif s.get("type") == "array" and isinstance(s.get("items"), dict):
            _verificar_esquema(s["items"])

    _verificar_esquema(schema)


@pytest.mark.parametrize("nombre", list(PLANTILLAS.keys()))
def test_plantillas_poseen_version_hash_valido(nombre: str) -> None:
    """Valida que cada plantilla exponga un hash de versión válido de 12 caracteres hexadecimales."""
    p = PLANTILLAS[nombre]
    assert "version_hash" in p
    assert isinstance(p["version_hash"], str)
    assert len(p["version_hash"]) == 12
    assert p["version"] == p["version_hash"]
