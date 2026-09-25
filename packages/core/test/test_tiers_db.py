"""Pruebas para KC-FAB-03-1: Admisión canónica de tier FABLE y eliminación de CASE."""

from __future__ import annotations

import pytest
from kallicode_core.llm.tiers import Tier, config_tier


def test_tier_fable_definido_en_enum() -> None:
    """Verifica que FABLE pertenezca formalmente a StrEnum Tier (BE-F10)."""
    assert Tier.FABLE == "fable"
    assert "fable" in [t.value for t in Tier]


def test_config_tier_admite_fable() -> None:
    """Verifica que config_tier resuelva la configuración del modelo para Fable sin fallar."""
    cfg = config_tier(Tier.FABLE)
    assert cfg is not None
    assert isinstance(cfg.modelo, str)
    assert len(cfg.modelo) > 0


@pytest.mark.parametrize("tier_val", ["flash", "pro", "fable"])
def test_tiers_validos_pasan_contrato(tier_val: str) -> None:
    """Valida que los tres tiers canónicos sean reconocidos sin error."""
    tier_enum = Tier(tier_val)
    assert tier_enum.value == tier_val
