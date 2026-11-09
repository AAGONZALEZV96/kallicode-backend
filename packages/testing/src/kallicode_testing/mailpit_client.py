"""Cliente de integración para Mailpit local (KC-QA-B5-11).

Provee un cliente asíncrono para inspeccionar, buscar, purgar y esperar correos
capturados por Mailpit en entorno local de pruebas (Cero-Nube, sin dependencias SMTP
externas).
"""

from __future__ import annotations

import asyncio
import smtplib
import time
from email.message import EmailMessage
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

# ==============================================================================
# DTOs Inmutables Pydantic v2 (frozen=True)
# ==============================================================================


class CapturedEmailDTO(BaseModel):
    """Representa un correo electrónico capturado por Mailpit."""

    model_config = ConfigDict(frozen=True)

    message_id: str = Field(description="Identificador único del mensaje en Mailpit")
    sender: str = Field(description="Dirección de correo del remitente")
    recipients: list[str] = Field(default_factory=list, description="Lista de destinatarios")
    subject: str = Field(description="Asunto del correo")
    text_body: str = Field(default="", description="Cuerpo del correo en texto plano")
    html_body: str = Field(default="", description="Cuerpo del correo en formato HTML")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Cabeceras HTTP/MIME del mensaje"
    )
    created_at: str = Field(default="", description="Marca de tiempo de recepción ISO-8601")


class MailpitSearchSummaryDTO(BaseModel):
    """Resumen inmutable de búsqueda de correos en Mailpit."""

    model_config = ConfigDict(frozen=True)

    total_found: int = Field(description="Total de correos que coinciden con la consulta")
    messages: list[CapturedEmailDTO] = Field(
        default_factory=list, description="Lista de correos capturados coincidentes"
    )


# ==============================================================================
# Helper de Envío SMTP Local Asíncrono
# ==============================================================================


async def send_email_smtp(
    subject: str,
    sender: str = "noreply@kallicode.dev",
    recipients: list[str] | None = None,
    text_body: str = "",
    html_body: str | None = None,
    headers: dict[str, str] | None = None,
    host: str = "localhost",
    port: int = 1025,
) -> None:
    """Envía un correo electrónico vía SMTP local a Mailpit de forma asíncrona."""
    to_list = recipients or ["developer@kallicode.dev"]

    def _sync_send() -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(to_list)

        if headers:
            for header_key, header_val in headers.items():
                msg[header_key] = header_val

        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(host, port, timeout=10.0) as smtp:
            smtp.send_message(msg)

    await asyncio.to_thread(_sync_send)


# ==============================================================================
# Cliente Principal: MailpitClient
# ==============================================================================


class MailpitClient:
    """Cliente asíncrono para interactuar con la API REST de Mailpit."""

    def __init__(
        self,
        api_base_url: str = "http://localhost:8025/api/v1",
        http_client: Any | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self._external_client = http_client if isinstance(http_client, httpx.AsyncClient) else None
        self._internal_client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(timeout=10.0)
        return self._internal_client

    async def aclose(self) -> None:
        """Cierra el cliente HTTP interno si existe."""
        if self._internal_client is not None and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None

    async def is_healthy(self) -> bool:
        """Verifica si Mailpit está disponible y respondiendo en la URL configurada."""
        try:
            client = self._get_client()
            resp = await client.get(f"{self.api_base_url}/info")
            return resp.status_code == 200
        except Exception:
            return False

    async def __aenter__(self) -> MailpitClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.aclose()

    async def delete_all_messages(self) -> bool:
        """Elimina todos los mensajes del buzón de Mailpit (purga atómica)."""
        client = self._get_client()
        url = f"{self.api_base_url}/messages"
        resp = await client.delete(url)
        return resp.status_code < 400

    async def get_message_by_id(self, message_id: str) -> CapturedEmailDTO:
        """Obtiene el detalle completo de un mensaje por su ID de Mailpit."""
        client = self._get_client()
        msg_url = f"{self.api_base_url}/message/{message_id}"
        hdr_url = f"{self.api_base_url}/message/{message_id}/headers"

        resp_msg = await client.get(msg_url)
        resp_msg.raise_for_status()
        msg_data: dict[str, Any] = resp_msg.json()

        # Obtener cabeceras detalladas
        headers_dict: dict[str, str] = {}
        resp_hdr = await client.get(hdr_url)
        if resp_hdr.status_code == 200:
            raw_headers = resp_hdr.json()
            if isinstance(raw_headers, dict):
                for k, v in raw_headers.items():
                    val = v[0] if isinstance(v, list) and v else str(v)
                    headers_dict[k] = val
                    headers_dict[k.lower()] = val
                    # Normalizaciones canónicas útiles para KalliCode
                    if k.lower() == "x-kallicode-tenant-id":
                        headers_dict["X-KalliCode-Tenant-ID"] = val
                    elif k.lower() == "x-ticket-id":
                        headers_dict["X-Ticket-ID"] = val

        # Procesar destinatarios
        to_field = msg_data.get("To", [])
        recipients: list[str] = []
        if isinstance(to_field, list):
            for item in to_field:
                if isinstance(item, dict) and "Address" in item:
                    recipients.append(str(item["Address"]))
                elif isinstance(item, str):
                    recipients.append(item)

        # Procesar remitente
        from_field = msg_data.get("From", {})
        sender = ""
        if isinstance(from_field, dict):
            sender = str(from_field.get("Address", ""))
        elif isinstance(from_field, str):
            sender = from_field

        return CapturedEmailDTO(
            message_id=str(msg_data.get("ID", message_id)),
            sender=sender,
            recipients=recipients,
            subject=str(msg_data.get("Subject", "")),
            text_body=str(msg_data.get("Text", "")),
            html_body=str(msg_data.get("HTML", "")),
            headers=headers_dict,
            created_at=str(msg_data.get("Date") or msg_data.get("Created") or ""),
        )

    async def get_messages(self, limit: int = 50) -> list[CapturedEmailDTO]:
        """Obtiene la lista de correos capturados con sus detalles completos."""
        client = self._get_client()
        url = f"{self.api_base_url}/messages?limit={limit}"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        raw_messages: list[dict[str, Any]] = data.get("messages", [])
        if not raw_messages:
            return []

        # Recuperar detalle completo de cada mensaje de forma concurrente
        tasks = [self.get_message_by_id(str(m["ID"])) for m in raw_messages if "ID" in m]
        captured_messages = await asyncio.gather(*tasks)
        return list(captured_messages)

    async def search_messages(self, query: str) -> MailpitSearchSummaryDTO:
        """Busca correos en Mailpit según la consulta especificada."""
        client = self._get_client()
        url = f"{self.api_base_url}/search?query={query}"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        raw_messages: list[dict[str, Any]] = data.get("messages", [])
        if not raw_messages:
            return MailpitSearchSummaryDTO(total_found=0, messages=[])

        tasks = [self.get_message_by_id(str(m["ID"])) for m in raw_messages if "ID" in m]
        captured = await asyncio.gather(*tasks)
        return MailpitSearchSummaryDTO(
            total_found=len(captured),
            messages=list(captured),
        )

    async def wait_for_email(
        self,
        subject_contains: str,
        recipient: str | None = None,
        timeout_sec: float = 5.0,
    ) -> CapturedEmailDTO:
        """Espera asíncronamente hasta que un correo coincidente arribe a Mailpit."""
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout_sec:
            messages = await self.get_messages(limit=50)
            for msg in messages:
                if subject_contains in msg.subject:
                    if recipient is None or recipient in msg.recipients:
                        return msg
            await asyncio.sleep(0.15)

        raise TimeoutError(
            f"No se recibió correo con asunto '{subject_contains}'"
            + (f" para '{recipient}'" if recipient else "")
            + f" tras {timeout_sec:.1f}s de espera."
        )
