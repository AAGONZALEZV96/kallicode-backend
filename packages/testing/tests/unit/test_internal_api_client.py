"""
Ticket: KC-QA-B4-09
Suite de pruebas unitarias y de resiliencia para InternalAPIClient (internal_api_client.py).
Valida:
1. test_generacion_idempotency_key_determinista: Estabilidad y unicidad de clave Idempotency-Key (QA-F13).
2. test_envio_exitoso_evidencias: Notificación exitosa 201 Created y mapeo de next_stage='gate2'.
3. test_reintentos_exponential_backoff_errores_5xx: Reintentos resilientes con backoff ante errores 5xx (QA-F05).
4. test_manejo_422_matriz_incompleta: Respuesta 422 sin reintentar y retorno de error controlado.
5. test_manejo_409_etapa_inconsistente: Respuesta 409 sin reintentar y bloqueo de etapa.
6. test_agotamiento_reintentos_errores_red: Tolerancia ante fallos persistentes de transporte.
7. test_inmutabilidad_dtos_y_errores_4xx: Validación de contratos inmutables frozen=True y errores 400.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from kallicode_testing.internal_api_client import (
    InternalAPIClient,
    NotificationResponseDTO,
    QAEvidencePayloadDTO,
)
from pydantic import ValidationError


@pytest.fixture
def sample_payload() -> QAEvidencePayloadDTO:
    return QAEvidencePayloadDTO(
        ticket_id="KC-1042",
        corrida=1,
        matriz=[
            {
                "criterio_id": "AC-01",
                "resultado": "pasa",
                "extracto_fallo": None,
                "evidencias": [
                    {
                        "tipo": "screenshot",
                        "url": "http://localhost:10000/devstoreaccount1/evidence/ac-01.png",
                        "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                    }
                ],
            },
            {
                "criterio_id": "AC-02",
                "resultado": "pasa",
                "extracto_fallo": None,
                "evidencias": [],
            },
        ],
        regresion={
            "dirigidos": 2,
            "dirigidos_ok": 2,
            "suite_cliente": 10,
            "cliente_ok": 10,
        },
        flaky=None,
    )


# ---------------------------------------------------------------------------
# Prueba 1: test_generacion_idempotency_key_determinista
# ---------------------------------------------------------------------------
def test_generacion_idempotency_key_determinista() -> None:
    """
    Prueba 1: Generar múltiples veces la misma clave para tenant, ticket y corrida.
    Validaciones:
    - La clave es idéntica entre ejecuciones (QA-F13).
    - Longitud exacta de 64 caracteres hexadecimales (SHA-256).
    - Varía si cambia el número de corrida, el ticket_id o el tenant_id.
    """
    k1 = InternalAPIClient.generate_idempotency_key(
        ticket_id="KC-1042", corrida=1, tenant_id="tenant-prod"
    )
    k2 = InternalAPIClient.generate_idempotency_key(
        ticket_id="KC-1042", corrida=1, tenant_id="tenant-prod"
    )
    k3_otra_corrida = InternalAPIClient.generate_idempotency_key(
        ticket_id="KC-1042", corrida=2, tenant_id="tenant-prod"
    )
    k4_otro_ticket = InternalAPIClient.generate_idempotency_key(
        ticket_id="KC-1043", corrida=1, tenant_id="tenant-prod"
    )
    k5_otro_tenant = InternalAPIClient.generate_idempotency_key(
        ticket_id="KC-1042", corrida=1, tenant_id="tenant-beta"
    )

    assert k1 == k2
    assert len(k1) == 64
    assert k1 != k3_otra_corrida
    assert k1 != k4_otro_ticket
    assert k1 != k5_otro_tenant


# ---------------------------------------------------------------------------
# Prueba 2: test_envio_exitoso_evidencias
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_envio_exitoso_evidencias(sample_payload: QAEvidencePayloadDTO) -> None:
    """
    Prueba 2: Simular respuesta 201 Created del endpoint interno.
    Validaciones:
    - Retorno exitoso con success is True.
    - Mapeo correcto de next_stage ('gate2').
    - Cabeceras Authorization e Idempotency-Key enviadas al backend.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url, service_token="jwt-svc-valid-token")

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url, assert_all_called=True) as respx_mock:
            endpoint_route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence").respond(
                status_code=201,
                json={"corrida": 1, "siguiente": "gate2", "job_id": "job-100"},
            )

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is True
            assert res.status_code == 201
            assert res.next_stage == "gate2"
            assert res.job_id == "job-100"

            # Validar cabeceras requeridas
            assert endpoint_route.called
            last_request = endpoint_route.calls.last.request
            assert last_request.headers["Authorization"] == "Bearer jwt-svc-valid-token"
            assert "Idempotency-Key" in last_request.headers
            assert last_request.headers["X-Tenant-ID"] == "tenant-prod"


# ---------------------------------------------------------------------------
# Prueba 3: test_reintentos_exponential_backoff_errores_5xx
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reintentos_exponential_backoff_errores_5xx(
    sample_payload: QAEvidencePayloadDTO,
) -> None:
    """
    Prueba 3: Simular fallos transitorios 500 y 503 con recuperación en el 3er intento.
    Validaciones:
    - Realiza los reintentos esperados aplicando backoff (QA-F05).
    - Retorna exitosamente tras recuperarse el servicio.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(
        base_url=base_url,
        max_retries=3,
        initial_backoff_sec=0.01,  # Rápido para tests unitarios
    )

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url) as respx_mock:
            route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence")
            route.side_effect = [
                httpx.Response(500, json={"error": "Database deadlock"}),
                httpx.Response(503, json={"error": "Gateway timeout"}),
                httpx.Response(201, json={"corrida": 1, "siguiente": "gate2"}),
            ]

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is True
            assert res.status_code == 201
            assert res.next_stage == "gate2"
            assert route.call_count == 3


# ---------------------------------------------------------------------------
# Prueba 4: test_manejo_422_matriz_incompleta
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manejo_422_matriz_incompleta(sample_payload: QAEvidencePayloadDTO) -> None:
    """
    Prueba 4: Simular respuesta 422 MATRIZ_INCOMPLETA.
    Validaciones:
    - NO realiza reintentos (error semántico de validación).
    - Retorna success is False con error_code='MATRIZ_INCOMPLETA'.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url, max_retries=3, initial_backoff_sec=0.01)

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url) as respx_mock:
            route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence").respond(
                status_code=422,
                json={
                    "error": {
                        "codigo": "MATRIZ_INCOMPLETA",
                        "mensaje": "Faltan criterios en la matriz: ['AC-03'].",
                    }
                },
            )

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is False
            assert res.status_code == 422
            assert res.error_code == "MATRIZ_INCOMPLETA"
            assert "Faltan criterios" in (res.message or "")
            assert route.call_count == 1  # Cero reintentos


# ---------------------------------------------------------------------------
# Prueba 5: test_manejo_409_etapa_inconsistente
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manejo_409_etapa_inconsistente(sample_payload: QAEvidencePayloadDTO) -> None:
    """
    Prueba 5: Simular respuesta 409 ETAPA_INCONSISTENTE.
    Validaciones:
    - NO realiza reintentos.
    - Retorna success is False con error_code='ETAPA_INCONSISTENTE' y bloquea avance.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url, max_retries=3, initial_backoff_sec=0.01)

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url) as respx_mock:
            route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence").respond(
                status_code=409,
                json={
                    "codigo": "ETAPA_INCONSISTENTE",
                    "mensaje": "El ticket no tiene spec aprobada.",
                },
            )

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is False
            assert res.status_code == 409
            assert res.error_code == "ETAPA_INCONSISTENTE"
            assert route.call_count == 1  # Cero reintentos


# ---------------------------------------------------------------------------
# Prueba 6: test_agotamiento_reintentos_errores_red
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agotamiento_reintentos_errores_red(
    sample_payload: QAEvidencePayloadDTO,
) -> None:
    """
    Prueba 6: Simular errores de red persistentes (httpx.ConnectError).
    Validaciones:
    - Agota exactamente max_retries intentos.
    - Retorna NotificationResponseDTO con success=False y error_code='MAX_RETRIES_EXCEEDED'.
    - No lanza excepciones no controladas.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url, max_retries=3, initial_backoff_sec=0.01)

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url) as respx_mock:
            route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence")
            route.side_effect = httpx.ConnectError("Connection refused to internal backend")

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is False
            assert res.status_code == 503
            assert res.error_code == "MAX_RETRIES_EXCEEDED"
            assert "reintentos sin éxito" in (res.message or "")
            assert route.call_count == 3


# ---------------------------------------------------------------------------
# Prueba 7: test_inmutabilidad_dtos_y_errores_4xx
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inmutabilidad_dtos_y_errores_4xx(sample_payload: QAEvidencePayloadDTO) -> None:
    """
    Prueba 7: Validar inmutabilidad Pydantic (frozen=True) y manejo de errores 400.
    """
    # 1. Inmutabilidad DTOs
    with pytest.raises(ValidationError):
        sample_payload.ticket_id = "KC-9999"

    resp_dto = NotificationResponseDTO(
        status_code=201,
        success=True,
        job_id="job-1",
        next_stage="gate2",
    )
    with pytest.raises(ValidationError):
        resp_dto.success = False

    # 2. Error 400 Bad Request no se reintenta
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url, max_retries=3, initial_backoff_sec=0.01)

    async with httpx.AsyncClient(base_url=base_url) as mock_http:
        client._external_client = mock_http

        with respx.mock(base_url=base_url) as respx_mock:
            route = respx_mock.post("/internal/v1/tickets/KC-1042/evidence").respond(
                status_code=400,
                text="Malformed payload structure",
            )

            res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-prod")

            assert res.success is False
            assert res.status_code == 400
            assert route.call_count == 1


# ---------------------------------------------------------------------------
# Prueba 8: test_cliente_asincrono_por_defecto_sin_external_client
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cliente_asincrono_por_defecto_sin_external_client(
    sample_payload: QAEvidencePayloadDTO,
) -> None:
    """
    Prueba 8: Validar que cuando no se inyecta _external_client,
    InternalAPIClient instancie y use su propio httpx.AsyncClient internamente.
    """
    base_url = "http://localhost:8000"
    client = InternalAPIClient(base_url=base_url)

    with respx.mock(base_url=base_url) as respx_mock:
        respx_mock.post("/internal/v1/tickets/KC-1042/evidence").respond(
            status_code=200,
            json={"siguiente": "gate2", "job_id": "job-auto-client"},
        )

        res = await client.send_qa_evidence(sample_payload, tenant_id="tenant-auto")

        assert res.success is True
        assert res.status_code == 200
        assert res.next_stage == "gate2"
        assert res.job_id == "job-auto-client"
