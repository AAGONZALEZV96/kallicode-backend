"""Pruebas de unidad para el stub de modelos LLM."""

import json
import time

import pytest
from kallicode_testing.stubs.llm_stub import LLMStub

pytestmark = pytest.mark.asyncio


@pytest.fixture
def llm_stub() -> LLMStub:
    """Fixture que provee la instancia del stub LLM."""
    return LLMStub()


async def test_llm_stub_performance_and_flash(llm_stub: LLMStub) -> None:
    """Verifica la respuesta base (flash) y que se ejecute en < 50ms."""
    start_time = time.perf_counter()
    response_str = await llm_stub.generate_test_code("crea test unitario", model="flash")
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert elapsed_ms < 50.0, f"El stub superó los 50ms de latencia: {elapsed_ms:.2f}ms"

    data = json.loads(response_str)
    assert len(data["tests"]) == 1
    assert data["tests"][0]["name"] == "test_login_success"


async def test_llm_stub_pro_model(llm_stub: LLMStub) -> None:
    """Verifica que el modelo pro retorne pruebas e2e adicionales estructuradas."""
    response_str = await llm_stub.generate_test_code("crea e2e", model="pro")
    data = json.loads(response_str)
    assert len(data["tests"]) == 2
    assert any(t["type"] == "e2e" for t in data["tests"])


async def test_llm_stub_malformed_json_error(llm_stub: LLMStub) -> None:
    """Verifica la simulación de respuesta con JSON corrupto (Resiliencia de Parser)."""
    response_str = await llm_stub.generate_test_code("test", simulate_error="malformed_json")
    with pytest.raises(json.JSONDecodeError):
        json.loads(response_str)


async def test_llm_stub_syntax_error(llm_stub: LLMStub) -> None:
    """Verifica la simulación de respuesta con error de sintaxis en código Python."""
    response_str = await llm_stub.generate_test_code("test", simulate_error="syntax_error")
    data = json.loads(response_str)
    assert "assert True ==" in data["tests"][0]["code"]
