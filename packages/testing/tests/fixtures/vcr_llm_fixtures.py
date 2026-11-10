"""
Ticket: KC-QA-B3-04
Mock Server de Endpoints de Modelos con VCR / RESPX (vcr_llm_fixtures.py).
Intercepta de forma transparente todas las llamadas HTTP salientes hacia OpenAI,
Anthropic y TEI/Embeddings sirviendo casetes locales deterministas en < 10 ms
y bloqueando cualquier tráfico no registrado hacia Internet (Aislamiento Cero-Nube).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from kallicode_testing.utils.path_utils import resolve_safe_path

CASSETTES_DIR: Path = Path(__file__).resolve().parent / "casetes"

OPENAI_CLOUD_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_LOCAL_CHAT_URL = "http://localhost:8080/v1/chat/completions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
TEI_EMBEDDINGS_URL = "http://localhost:8080/v1/embeddings"
TEI_MODELS_URL = "http://localhost:8080/v1/models"


@lru_cache(maxsize=32)
def _read_cassette_cached(filename: str) -> str:
    """Lee el archivo JSON del casete validando confinamiento dentro de CASSETTES_DIR."""
    safe_path = resolve_safe_path(CASSETTES_DIR, filename)
    if not safe_path.is_file():
        raise FileNotFoundError(f"Casete VCR no encontrado: {safe_path}")
    return safe_path.read_text(encoding="utf-8")


def load_cassette(cassette_name: str) -> dict[str, Any]:
    """
    Carga y devuelve una respuesta determinista desde packages/testing/tests/fixtures/casetes/.
    Previene ataques de Path Traversal mediante resolve_safe_path.
    """
    if not cassette_name or not cassette_name.strip():
        raise ValueError("El nombre del casete no puede estar vacío.")

    cleaned = cassette_name.strip()
    filename = cleaned if cleaned.endswith(".json") else f"{cleaned}.json"
    raw_json = _read_cassette_cached(filename)
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise TypeError(f"El casete '{filename}' no contiene un objeto JSON válido.")
    return copy.deepcopy(parsed)


@pytest.fixture
def mock_llm_endpoints() -> Iterator[respx.MockRouter]:
    """
    Fixture principal que activa respx.mock(assert_all_called=False, assert_all_mocked=True)
    interceptando los endpoints de OpenAI, Anthropic y TEI con casetes locales deterministas.
    Cualquier petición HTTP saliente no registrada falla explícitamente (Cero-Nube).
    """
    openai_payload = load_cassette("openai_chat_completions")
    anthropic_payload = load_cassette("anthropic_messages")
    tei_embeddings_payload = load_cassette("tei_embeddings")
    tei_models_payload = load_cassette("tei_models")

    with respx.mock(assert_all_called=False, assert_all_mocked=True) as respx_mock:
        respx_mock.post(OPENAI_CLOUD_CHAT_URL).mock(
            return_value=httpx.Response(200, json=openai_payload)
        )
        respx_mock.post(OPENAI_LOCAL_CHAT_URL).mock(
            return_value=httpx.Response(200, json=openai_payload)
        )
        respx_mock.post(ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=anthropic_payload)
        )
        respx_mock.post(TEI_EMBEDDINGS_URL).mock(
            return_value=httpx.Response(200, json=tei_embeddings_payload)
        )
        respx_mock.get(TEI_MODELS_URL).mock(
            return_value=httpx.Response(200, json=tei_models_payload)
        )
        yield respx_mock
