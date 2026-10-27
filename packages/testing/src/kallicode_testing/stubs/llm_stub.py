"""Stub local determinista para la simulación de modelos LLM."""

import json
from typing import Literal

ModelType = Literal["flash", "pro", "fable"]
ErrorSimulationType = Literal["none", "syntax_error", "malformed_json"]


class LLMStub:
    """Simulador offline de respuestas LLM para generación de pruebas en el motor QA."""

    def __init__(self) -> None:
        """Inicializa el stub de LLM con respuestas base deterministas."""
        self._base_response = {
            "tests": [
                {
                    "name": "test_login_success",
                    "code": "def test_login_success():\n    assert True",
                    "type": "unit",
                }
            ]
        }

    async def generate_test_code(
        self, prompt: str, model: ModelType = "flash", simulate_error: ErrorSimulationType = "none"
    ) -> str:
        """
        Simula la generación de código de prueba basado en el modelo especificado.
        Retorna estructuras JSON simuladas en formato string.
        """
        # Simulación de resiliencia del parser: JSON roto sin cerrar
        if simulate_error == "malformed_json":
            return '{"tests": [{"name": "test_broken", "code": "def test(): pass"'

        # Simulación de resiliencia de ejecución: Código Python con error de sintaxis
        if simulate_error == "syntax_error":
            return json.dumps(
                {
                    "tests": [
                        {
                            "name": "test_syntax",
                            "code": "def test_error():\n    assert True ==",
                            "type": "unit",
                        }
                    ]
                }
            )

        # Respuesta exitosa variando la profundidad según el modelo
        response = self._base_response.copy()
        if model == "pro":
            response["tests"].append(
                {
                    "name": "test_e2e_flujo_critico",
                    "code": "async def test_flujo(page):\n    await page.goto('/')\n    assert await page.title() != ''",
                    "type": "e2e",
                }
            )
        elif model == "fable":
            response["tests"].append(
                {
                    "name": "test_fable_reasoning",
                    "code": "def test_edge_case():\n    assert 1 != 2",
                    "type": "integration",
                }
            )

        return json.dumps(response)
