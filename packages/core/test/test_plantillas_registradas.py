"""Registro de plantillas LLM definidas fuera del catálogo de la fábrica (KC-CM-08-2)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from kallicode_core.llm import plantillas as pl
from kallicode_core.llm.plantillas import (
    PLANTILLAS,
    PLANTILLAS_REGISTRADAS,
    calcular_hash_plantilla,
    plantilla,
    registrar_plantilla,
)
from kallicode_core.llm.tiers import Tier

PASO = "pruebas.registro"
SYSTEM = "You test the registration of external LLM templates. JSON only."


def _esquema(estricto: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "object", "properties": {"x": {"type": "string"}}}
    if estricto:
        item["additionalProperties"] = False
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": item}},
    }


def _registrar(**cambios: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Plantilla de prueba.",
        "entrada": {"x": "str"},
        "salida": _esquema(),
        "system": SYSTEM,
    }
    args.update(cambios)
    paso = args.pop("paso_id", PASO)
    return registrar_plantilla(paso, **args)


@pytest.fixture(autouse=True)
def _limpio() -> Iterator[None]:
    PLANTILLAS_REGISTRADAS.pop(PASO, None)
    yield
    PLANTILLAS_REGISTRADAS.pop(PASO, None)


def test_registrada_se_resuelve_con_version_hash_y_no_toca_el_catalogo() -> None:
    antes = len(PLANTILLAS)
    spec = _registrar()

    assert plantilla(PASO) is spec
    assert spec["version"] == spec["version_hash"] == calcular_hash_plantilla(SYSTEM, _esquema())
    assert spec["tier"] is Tier.FLASH and spec["escalable"] is True
    assert len(PLANTILLAS) == antes
    assert PASO not in PLANTILLAS


def test_registro_idempotente_y_version_distinta_rechazada() -> None:
    primera = _registrar()
    assert _registrar() is primera
    with pytest.raises(ValueError, match="otra versión"):
        _registrar(system=SYSTEM + " Changed.")


def test_no_puede_pisar_un_paso_de_la_fabrica() -> None:
    with pytest.raises(ValueError, match="catálogo de la fábrica"):
        _registrar(paso_id="triage.classify")


def test_esquema_no_estricto_rechazado_con_la_ruta() -> None:
    with pytest.raises(ValueError, match=r"\$\.properties\.items\.items"):
        _registrar(salida=_esquema(estricto=False))
    assert PASO not in PLANTILLAS_REGISTRADAS


def test_objetos_no_estrictos_recorre_listas_y_anidados() -> None:
    esquema = {"anyOf": [{"type": "null"}, {"type": "object"}]}
    assert pl._objetos_no_estrictos(esquema) == ["$.anyOf[1]"]
    assert pl._objetos_no_estrictos(_esquema()) == []


def test_paso_desconocido_lista_tambien_los_registrados() -> None:
    _registrar()
    with pytest.raises(KeyError, match=PASO):
        plantilla("paso.inexistente")
