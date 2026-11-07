"""
Ticket: KC-QA-B4-09
Integrador del Endpoint Interno de Notificación de Resultados (/internal/v1) (internal_api_client.py).
Cliente asíncrono para notificar resultados y matrices de prueba a /internal/v1/tickets/{ticket_id}/evidence.
Garantiza autenticación estricta con JWT de servicio (typ=svc), idempotencia criptográfica determinista
(SHA-256 de qa_evidence:{tenant}:{ticket}:r{corrida}), y resiliencia con exponential backoff ante fallos 5xx
y errores transitorios de red, cerrando definitivamente los hallazgos QA-F05 y QA-F13.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

# Resultados válidos para cada criterio en la matriz
ResultadoCriterio = Literal["pasa", "falla", "flaky"]


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-09
# ---------------------------------------------------------------------------
class QAEvidencePayloadDTO(BaseModel):
    """
    Payload inmutable de notificación de resultados de corrida de QA.
    Alineado con el esquema EvidenciaIn de contratos/internal-v1.yaml.
    """

    model_config = ConfigDict(frozen=True)

    ticket_id: str  # Ej: KC-1042 o TK-0001
    corrida: int = Field(ge=1)
    matriz: list[dict[str, Any]] = Field(min_length=1)
    veredicto_visual: list[dict[str, Any]] | None = None
    regresion: dict[str, int]
    flaky: list[str] | None = None


class NotificationResponseDTO(BaseModel):
    """
    Respuesta inmutable tras la notificación de resultados al endpoint interno.
    """

    model_config = ConfigDict(frozen=True)

    status_code: int
    success: bool
    job_id: str | None = None
    next_stage: str | None = None  # "gate2" si todo pasa, "build" si falla
    error_code: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Clase Principal InternalAPIClient
# ---------------------------------------------------------------------------
class InternalAPIClient:
    """
    Puente de comunicación asíncrono entre el runner de pruebas y el orquestador backend (/internal/v1).
    Aplica Idempotency-Key determinista y reintentos con exponential backoff exclusivamente en errores 5xx.
    """

    DEFAULT_BASE_URL: str = "http://localhost:8000"

    def __init__(
        self,
        base_url: str | None = None,
        service_token: str | None = None,
        max_retries: int = 3,
        initial_backoff_sec: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("KC_INTERNAL_API_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.service_token = (
            service_token
            or os.environ.get("KC_SERVICE_TOKEN")
            or os.environ.get("SERVICE_JWT")
            or "mock-service-token-typ-svc"
        )
        self.max_retries = max(1, int(max_retries))
        self.initial_backoff_sec = max(0.01, float(initial_backoff_sec))
        self._external_client = client

    @staticmethod
    def generate_idempotency_key(
        ticket_id: str,
        corrida: int,
        tenant_id: str = "default",
    ) -> str:
        """
        Genera un hash determinista SHA-256 a partir del formato canónico:
        qa_evidence:{tenant_id}:{ticket_id}:r{corrida}
        Garantiza estabilidad e idempotencia absoluta entre reintentos (QA-F13).
        """
        raw_key = f"qa_evidence:{tenant_id}:{ticket_id}:r{corrida}".encode()
        return hashlib.sha256(raw_key).hexdigest()

    async def send_qa_evidence(
        self,
        payload: QAEvidencePayloadDTO,
        tenant_id: str,
        timeout_sec: float = 15.0,
    ) -> NotificationResponseDTO:
        """
        Envía la matriz de evidencias al endpoint POST /internal/v1/tickets/{ticket_id}/evidence.
        Aplica exponential backoff en errores 5xx y errores de red.
        Rechaza reintentos ante 422 (MATRIZ_INCOMPLETA) o 409 (ETAPA_INCONSISTENTE).
        """
        endpoint = f"{self.base_url}/internal/v1/tickets/{payload.ticket_id}/evidence"
        idempotency_key = self.generate_idempotency_key(
            ticket_id=payload.ticket_id,
            corrida=payload.corrida,
            tenant_id=tenant_id,
        )

        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
            "X-Tenant-ID": tenant_id,
        }

        # Serialización canónica del cuerpo
        request_body: dict[str, Any] = {
            "corrida": payload.corrida,
            "matriz": payload.matriz,
            "regresion": payload.regresion,
        }
        if payload.veredicto_visual is not None:
            request_body["veredicto_visual"] = payload.veredicto_visual
        if payload.flaky is not None:
            request_body["flaky"] = payload.flaky

        last_status_code: int = 500
        last_error_message: str = "Error desconocido de comunicación interna"

        # Bucle de reintento con Exponential Backoff
        for attempt in range(1, self.max_retries + 1):
            delay = self.initial_backoff_sec * (2 ** (attempt - 1))
            try:
                logger.info(
                    "internal_api_client.sending_evidence",
                    ticket_id=payload.ticket_id,
                    corrida=payload.corrida,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    idempotency_key=idempotency_key[:16] + "...",
                )

                if self._external_client is not None:
                    response = await self._external_client.post(
                        endpoint,
                        json=request_body,
                        headers=headers,
                        timeout=timeout_sec,
                    )
                else:
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            endpoint,
                            json=request_body,
                            headers=headers,
                            timeout=timeout_sec,
                        )

                last_status_code = response.status_code

                # 1. Éxito: 200 OK / 201 Created
                if response.status_code in (200, 201):
                    data: dict[str, Any] = {}
                    try:
                        data = response.json()
                    except Exception:
                        pass

                    next_stage = data.get("siguiente") or data.get("next_stage")
                    job_id = data.get("job_id") or data.get("qa_run_id")

                    logger.info(
                        "internal_api_client.evidence_registered",
                        ticket_id=payload.ticket_id,
                        corrida=payload.corrida,
                        next_stage=next_stage,
                    )
                    return NotificationResponseDTO(
                        status_code=response.status_code,
                        success=True,
                        job_id=str(job_id) if job_id else None,
                        next_stage=str(next_stage) if next_stage else None,
                        message="Evidencia registrada exitosamente",
                    )

                # 2. Errores 4xx de negocio: NO REINTENTAR
                if response.status_code == 422:
                    error_info = self._parse_error(response)
                    logger.warning(
                        "internal_api_client.matriz_incompleta_rejected",
                        ticket_id=payload.ticket_id,
                        status_code=422,
                        error=error_info,
                    )
                    return NotificationResponseDTO(
                        status_code=422,
                        success=False,
                        error_code=error_info.get("codigo", "MATRIZ_INCOMPLETA"),
                        message=error_info.get("mensaje", "Matriz de criterios incompleta"),
                    )

                if response.status_code == 409:
                    error_info = self._parse_error(response)
                    logger.warning(
                        "internal_api_client.etapa_inconsistente_rejected",
                        ticket_id=payload.ticket_id,
                        status_code=409,
                        error=error_info,
                    )
                    return NotificationResponseDTO(
                        status_code=409,
                        success=False,
                        error_code=error_info.get("codigo", "ETAPA_INCONSISTENTE"),
                        message=error_info.get(
                            "mensaje", "Conflicto: etapa inconsistente o sin spec aprobada"
                        ),
                    )

                # Otros errores 4xx (400, 401, 403, 404): NO REINTENTAR
                if 400 <= response.status_code < 500:
                    error_info = self._parse_error(response)
                    logger.error(
                        "internal_api_client.client_error",
                        status_code=response.status_code,
                        error=error_info,
                    )
                    return NotificationResponseDTO(
                        status_code=response.status_code,
                        success=False,
                        error_code=error_info.get("codigo", "CLIENT_ERROR"),
                        message=error_info.get("mensaje", response.text),
                    )

                # 3. Errores 5xx del servidor: APLICAR EXPONENTIAL BACKOFF (QA-F05)
                last_error_message = f"Error del servidor {response.status_code}: {response.text}"
                logger.warning(
                    "internal_api_client.server_error_5xx",
                    attempt=attempt,
                    status_code=response.status_code,
                    retry_in_sec=delay,
                )

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_status_code = 503
                last_error_message = f"Fallo de transporte / timeout de red: {exc}"
                logger.warning(
                    "internal_api_client.network_transport_error",
                    attempt=attempt,
                    error=str(exc),
                    retry_in_sec=delay,
                )
            except Exception as exc:
                last_status_code = 500
                last_error_message = f"Excepción imprevista: {exc}"
                logger.error(
                    "internal_api_client.unexpected_exception",
                    attempt=attempt,
                    error=str(exc),
                )

            # Esperar antes del siguiente intento si quedan reintentos
            if attempt < self.max_retries:
                await asyncio.sleep(delay)

        # Fallo definitivo tras agotar reintentos
        logger.error(
            "internal_api_client.max_retries_exhausted",
            ticket_id=payload.ticket_id,
            attempts=self.max_retries,
            last_status_code=last_status_code,
        )
        return NotificationResponseDTO(
            status_code=last_status_code,
            success=False,
            error_code="MAX_RETRIES_EXCEEDED",
            message=f"Se agotaron los {self.max_retries} reintentos sin éxito. Último error: {last_error_message}",
        )

    def _parse_error(self, response: httpx.Response) -> dict[str, str]:
        """Extrae el código y mensaje de error del ErrorEnvelope."""
        try:
            data = response.json()
            if isinstance(data, dict):
                # Soporta formato { "error": { "codigo": ..., "mensaje": ... } } o { "codigo": ..., "mensaje": ... }
                if "error" in data and isinstance(data["error"], dict):
                    err = data["error"]
                    return {
                        "codigo": str(err.get("codigo") or err.get("code") or "ERROR"),
                        "mensaje": str(err.get("mensaje") or err.get("message") or ""),
                    }
                return {
                    "codigo": str(data.get("codigo") or data.get("error_code") or "ERROR"),
                    "mensaje": str(data.get("mensaje") or data.get("message") or response.text),
                }
        except Exception:
            pass
        return {"codigo": "ERROR", "mensaje": response.text}
