"""Pruebas de contrato para cuerpos malformados de /internal/v1."""

from __future__ import annotations

import pytest
import schemathesis
from fastapi.testclient import TestClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main import app


@pytest.fixture(autouse=True)
def servicio_interno_de_prueba():
    """Evita JWT real para que la petición alcance la validación Pydantic."""
    anterior = app.dependency_overrides.get(servicio_actual)
    app.dependency_overrides[servicio_actual] = lambda: ServicioActual(
        servicio="orquestador",
        tenant_id="tenant-test",
        linea=None,
    )
    yield
    if anterior is None:
        app.dependency_overrides.pop(servicio_actual, None)
    else:
        app.dependency_overrides[servicio_actual] = anterior


client = TestClient(app)
schema = schemathesis.openapi.from_asgi("/openapi.json", app).include(
    path_regex=r"^/internal/v1/tickets/[^/]+/(spec-versions|evidence|findings)$"
)


@pytest.mark.parametrize(
    ("ruta", "cuerpo", "campo_esperado"),
    [
        (
            "/internal/v1/tickets/TK-0001/evidence",
            {
                "corrida": 1,
                "matriz": [
                    {
                        "criterio_id": "AC-1",
                        # Falta resultado.
                        "evidencias": [],
                    }
                ],
                "veredicto_visual": [],
                "regresion": {},
                "flaky": [],
            },
            "resultado",
        ),
        (
            "/internal/v1/tickets/TK-0001/evidence",
            {
                "corrida": 1,
                "matriz": [
                    {
                        # No cumple el patrón AC-<número>.
                        "criterio_id": "QA-1",
                        "resultado": "pasa",
                        "evidencias": [],
                    }
                ],
                "veredicto_visual": [],
                "regresion": {},
                "flaky": [],
            },
            "criterio_id",
        ),
        (
            "/internal/v1/tickets/TK-0001/evidence",
            {
                "corrida": 1,
                "matriz": [
                    {
                        "criterio_id": "AC-1",
                        # Valor no permitido.
                        "resultado": "terminado",
                        "evidencias": [],
                    }
                ],
                "veredicto_visual": [],
                "regresion": {},
                "flaky": [],
            },
            "resultado",
        ),
        (
            "/internal/v1/tickets/TK-0001/evidence",
            {
                "corrida": 1,
                "matriz": [
                    {
                        "criterio_id": "AC-1",
                        "resultado": "falla",
                        "evidencias": [],
                    }
                ],
                "veredicto_visual": [],
                "regresion": {},
                "flaky": [],
            },
            "extracto_fallo",
        ),
        (
            "/internal/v1/tickets/TK-0001/findings",
            {
                "hallazgos": [],
                "veredicto_cambio": "approved",
                "tickets_proactivos": [
                    {
                        # Falta titulo.
                        "cuerpo": "Descripción del hallazgo.",
                    }
                ],
            },
            "titulo",
        ),
        (
            "/internal/v1/tickets/TK-0001/findings",
            {
                "hallazgos": [],
                "veredicto_cambio": "approved",
                "tickets_proactivos": [
                    {
                        "titulo": "Hallazgo de seguridad",
                        # Falta cuerpo.
                    }
                ],
            },
            "cuerpo",
        ),
    ],
)
def test_cuerpos_malformados_responden_422(
    ruta: str,
    cuerpo: dict,
    campo_esperado: str,
) -> None:
    """Los cuerpos inválidos deben rechazarse durante la validación Pydantic."""
    respuesta = client.post(
        ruta,
        json=cuerpo,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "test-cuerpo-malformado",
        },
    )

    assert respuesta.status_code == 422
    assert campo_esperado in respuesta.text


def test_cuerpo_malformado_no_produce_respuesta_500() -> None:
    """
    Un cuerpo inválido nunca debe convertirse en un error interno del servidor.

    Este caso reproduce el problema original: la matriz contiene un elemento
    sin `resultado`. Antes podía llegar al router y provocar KeyError.
    """
    cuerpo = {
        "corrida": 1,
        "matriz": [
            {
                "criterio_id": "AC-1",
                "evidencias": [],
            }
        ],
        "veredicto_visual": [],
        "regresion": {},
        "flaky": [],
    }

    respuesta = client.post(
        "/internal/v1/tickets/TK-0001/evidence",
        json=cuerpo,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "test-no-500",
        },
    )

    assert respuesta.status_code < 500
    assert respuesta.status_code == 422


@schema.parametrize()
def test_openapi_internal_no_produce_5xx_para_casos_generados(case) -> None:
    """
    Schemathesis usa el OpenAPI generado por FastAPI para seleccionar las rutas.

    Se sustituye el cuerpo generado por un objeto vacío para ejercer la
    validación de entrada sin ejecutar lógica de base de datos.
    """
    case.body = {}
    respuesta = case.call()

    assert respuesta.status_code < 500, (
        f"{case.method} {case.path} respondió {respuesta.status_code}: {respuesta.text}"
    )
