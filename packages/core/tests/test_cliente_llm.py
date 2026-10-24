from unittest.mock import patch

import httpx
import pytest
import respx
from kallicode_core.llm.cliente import _circuitos, ejecutar_paso
from kallicode_core.llm.tiers import Tier

# Fixture falso para aislar el test del catálogo real de plantillas
PLANTILLA_MOCK = {
    "tier": Tier.FLASH,
    "version": "1.0.0",
    "salida": {"type": "object", "properties": {"status": {"type": "string"}}},
    "system": "Eres un asistente de pruebas.",
    "escalable": True,
}


@pytest.fixture(autouse=True)
def limpiar_circuitos():
    """Limpia el estado global de los circuitos antes de cada test."""
    _circuitos.clear()
    yield


@pytest.mark.asyncio
@respx.mock
@patch("kallicode_core.llm.cliente.plantilla", return_value=PLANTILLA_MOCK)
async def test_llm_reintento_500_y_acumula_tokens(mock_plantilla):
    """Prueba que un 500 hace un reintento, no escala de tier y suma los tokens de ambos intentos."""

    ruta = respx.post(url__regex=r".*/chat/completions")

    ruta.side_effect = [
        # Intento 1: Falla con 500, pero facturó 10 tokens de entrada
        httpx.Response(500, json={"usage": {"prompt_tokens": 10, "completion_tokens": 0}}),
        # Intento 2: Éxito con 200, factura 10 de entrada y 5 de salida
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"status": "ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ),
    ]

    resultado = await ejecutar_paso("test.step", {"msg": "hola"})

    assert ruta.call_count == 2, "Debió reintentar exactamente una vez tras el 500"
    assert resultado.tier == Tier.FLASH, "No debió escalar de tier"
    assert resultado.tokens_in == 20, "Debió sumar 10 del fallo + 10 del éxito"
    assert resultado.tokens_out == 5
    assert len(resultado.intentos) == 1
    assert resultado.intentos[0]["resultado"] == "proveedor_fallo"


@pytest.mark.asyncio
@respx.mock
@patch("kallicode_core.llm.cliente.plantilla", return_value=PLANTILLA_MOCK)
async def test_llm_error_400_aborta_sin_reintento(mock_plantilla):
    """Prueba que un 401 (mala API key) aborta de inmediato sin escalar ni reintentar."""

    ruta = respx.post(url__regex=r".*/chat/completions")
    ruta.return_value = httpx.Response(401, text="Unauthorized API Key")

    with pytest.raises(ValueError, match="Error de configuración \\(4xx\\)"):
        await ejecutar_paso("test.step", {"msg": "hola"})

    assert ruta.call_count == 1, "Un error 4xx nunca debe reintentarse"


@pytest.mark.asyncio
@respx.mock
@patch("kallicode_core.llm.cliente.plantilla", return_value=PLANTILLA_MOCK)
@patch("kallicode_core.llm.cliente.get_settings")
async def test_llm_circuit_breaker_abre_y_escala(mock_settings, mock_plantilla):
    """Prueba que múltiples fallos de red abren el circuito y obligan a escalar al siguiente tier."""

    mock_settings.return_value.llm_circuit_umbral = 2
    mock_settings.return_value.llm_circuit_ventana = 60.0
    mock_settings.return_value.llm_timeout_s = 5.0

    ruta_flash = respx.post(url__regex=r".*/chat/completions")
    ruta_flash.side_effect = httpx.ConnectTimeout("Timeout de red")

    ruta_fable = respx.post(url__regex=r".*/messages")
    ruta_fable.return_value = httpx.Response(
        200,
        json={
            "content": [{"text": '{"status": "rescatado por fable"}'}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        },
    )

    # Ejecución 1: Falla Flash (Intento 1 y Reintento). Circuit Breaker se ABRE.
    await ejecutar_paso("test.step", {"msg": "falla_red"})

    # Ejecución 2: El circuito de Flash está abierto. Salta directo a Pro/Fable.
    resultado2 = await ejecutar_paso("test.step", {"msg": "circuito_abierto"})

    assert resultado2.intentos[0]["resultado"] == "circuito_abierto"
    assert resultado2.intentos[0]["tier"] == "flash"
