"""Pruebas para KC-FAB-03-6 validando los mocks de los 3 tiers con respx."""

import asyncio
import sys
from pathlib import Path

import httpx
import respx
from jsonschema import validate
from kallicode_core.llm.plantillas import PLANTILLAS

from .mocks_tiers import cargar_grabaciones, configurar_mocks_tiers

# Asegurar visibilidad del directorio de tests para imports auxiliares
TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))


def test_mocks_3_tiers_responden_correctamente() -> None:
    """Verifica que los mocks de los 3 tiers respondan y cumplan sus contratos JSON Schema."""

    async def _ejecutar() -> None:
        base_url = "https://api.kallicode.internal/llm"
        with respx.mock(assert_all_called=False) as respx_mock:
            configurar_mocks_tiers(respx_mock, base_url=base_url)
            grabaciones = cargar_grabaciones()

            async with httpx.AsyncClient() as client:
                for tier in ["flash", "pro", "fable"]:
                    resp = await client.post(f"{base_url}/{tier}")
                    assert resp.status_code == 200
                    data = resp.json()

                    # Validar contrato contra el JSON Schema de la plantilla
                    step_id = grabaciones[tier]["step_id"]
                    schema = PLANTILLAS[step_id]["salida"]
                    validate(instance=data, schema=schema)

    asyncio.run(_ejecutar())
