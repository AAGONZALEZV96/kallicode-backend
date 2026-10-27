"""Mocks de inferencia LLM con respx para CI (KC-FAB-03-6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import respx
from httpx import Response

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mock_tiers_responses.json"


def cargar_grabaciones() -> dict[str, Any]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def configurar_mocks_tiers(
    router: respx.MockRouter, base_url: str = "https://api.kallicode.internal/llm"
) -> None:
    """Configura rutas mock para los 3 tiers LLM usando respx."""
    datos = cargar_grabaciones()

    for tier, cfg in datos.items():
        router.post(f"{base_url}/{tier}").mock(
            return_value=Response(
                status_code=cfg["status_code"],
                json=cfg["payload"],
            )
        )
