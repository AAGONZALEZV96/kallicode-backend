"""Pruebas de integración de notificaciones por email con Mailpit local (KC-QA-B5-11).

Certifica de forma 100% automatizada, local y Cero-Nube que el backend y los componentes
de KalliCode emiten y capturan correctamente las notificaciones por correo electrónico:
- Alertas de veredicto QA.
- Solicitudes de firma en Gates.
- Avisos de cuota comercial.
- Aislamiento multi-tenant mediante cabeceras X-KalliCode-Tenant-ID y X-Ticket-ID.
- Purga atómica del buzón sintético para evitar contaminación cruzada entre pruebas.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

import httpx
import pytest
from kallicode_testing.mailpit_client import (
    CapturedEmailDTO,
    MailpitClient,
    MailpitSearchSummaryDTO,
    send_email_smtp,
)
from pydantic import ValidationError

# ==============================================================================
# Fixtures de Aislamiento y Ciclo de Vida
# ==============================================================================


@pytest.fixture
async def mailpit_client() -> AsyncGenerator[MailpitClient, None]:
    """Provee una instancia limpia de MailpitClient para cada test."""
    client = MailpitClient()
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def aislar_buzon_mailpit(
    mailpit_client: MailpitClient,
) -> AsyncGenerator[None, None]:
    """Purga atómicamente el buzón de Mailpit antes y después de cada prueba."""
    await mailpit_client.delete_all_messages()
    yield
    await mailpit_client.delete_all_messages()


# ==============================================================================
# 1. test_envio_y_captura_notificacion_veredicto_qa
# ==============================================================================


@pytest.mark.asyncio
async def test_envio_y_captura_notificacion_veredicto_qa(
    mailpit_client: MailpitClient,
) -> None:
    """Certifica el envío y captura REST de una alerta de veredicto QA."""
    destinatario = "qa-lead@kallicode.dev"
    asunto = "Alerta de Veredicto QA - Gate 1 Aprobado (Ticket KC-1042)"
    cuerpo_texto = "El veredicto del Gate 1 para el ticket KC-1042 es: APROBADO."
    cuerpo_html = (
        "<html><body>"
        "<h2>Veredicto de Calidad: Gate 1</h2>"
        "<p>El ticket <strong>KC-1042</strong> ha sido <span style='color:green'>APROBADO</span>.</p>"
        "</body></html>"
    )

    await send_email_smtp(
        subject=asunto,
        sender="notifications@kallicode.dev",
        recipients=[destinatario],
        text_body=cuerpo_texto,
        html_body=cuerpo_html,
        headers={
            "X-KalliCode-Tenant-ID": "tenant-corp-prod",
            "X-Ticket-ID": "KC-1042",
        },
    )

    mensajes = await mailpit_client.get_messages(limit=10)
    assert len(mensajes) == 1, f"Se esperaba 1 mensaje en Mailpit, se hallaron {len(mensajes)}"

    email = mensajes[0]
    assert email.subject == asunto
    assert destinatario in email.recipients
    assert email.sender == "notifications@kallicode.dev"
    assert "El veredicto del Gate 1" in email.text_body
    assert "APROBADO" in email.html_body
    assert "<span style='color:green'>APROBADO</span>" in email.html_body
    assert email.created_at != ""


# ==============================================================================
# 2. test_verificacion_cabeceras_multitenant_x_kallicode_headers
# ==============================================================================


@pytest.mark.asyncio
async def test_verificacion_cabeceras_multitenant_x_kallicode_headers(
    mailpit_client: MailpitClient,
) -> None:
    """Verifica la existencia y validez de las cabeceras X-KalliCode-Tenant-ID y X-Ticket-ID."""
    tenant_esperado = "tenant-fintech-sec-01"
    ticket_esperado = "KC-9901"

    await send_email_smtp(
        subject="Solicitud de Firma de Gate 3 - Despliegue Producción",
        sender="gates@kallicode.dev",
        recipients=["architect@fintech.com"],
        text_body="Se requiere firma de arquitecto para Gate 3.",
        html_body="<p>Se requiere firma para despliegue de <strong>KC-9901</strong>.</p>",
        headers={
            "X-KalliCode-Tenant-ID": tenant_esperado,
            "X-Ticket-ID": ticket_esperado,
            "X-Priority": "1",
        },
    )

    email = await mailpit_client.wait_for_email(
        subject_contains="Solicitud de Firma de Gate 3",
        recipient="architect@fintech.com",
    )

    # Validar cabeceras multi-tenant obligatorias
    assert "X-KalliCode-Tenant-ID" in email.headers
    assert email.headers["X-KalliCode-Tenant-ID"] == tenant_esperado
    assert "X-Ticket-ID" in email.headers
    assert email.headers["X-Ticket-ID"] == ticket_esperado

    # Validar acceso insensible a mayúsculas/minúsculas
    assert email.headers.get("x-kallicode-tenant-id") == tenant_esperado
    assert email.headers.get("x-ticket-id") == ticket_esperado


# ==============================================================================
# 3. test_busqueda_y_espera_asincrona_de_correo
# ==============================================================================


@pytest.mark.asyncio
async def test_busqueda_y_espera_asincrona_de_correo(
    mailpit_client: MailpitClient,
) -> None:
    """Valida la espera no-bloqueante wait_for_email, búsquedas y timeouts controlados."""

    # A. Búsqueda y espera concurrente cuando el correo arriba con retraso
    async def enviar_con_retraso() -> None:
        await asyncio.sleep(0.3)
        await send_email_smtp(
            subject="Aviso de Cuota Comercial - 80% LOC Consumidas",
            sender="billing@kallicode.dev",
            recipients=["billing-contact@empresa.com"],
            text_body="Has alcanzado el 80% de tus LOC mensuales.",
            html_body="<p>Aviso: 80% de cuota utilizada.</p>",
            headers={"X-KalliCode-Tenant-ID": "tenant-empresa-growth"},
        )

    t0 = time.monotonic()
    tarea_envio = asyncio.create_task(enviar_con_retraso())

    email_arribado = await mailpit_client.wait_for_email(
        subject_contains="Aviso de Cuota Comercial",
        recipient="billing-contact@empresa.com",
        timeout_sec=4.0,
    )
    t_espera = time.monotonic() - t0
    await tarea_envio

    assert "80% LOC Consumidas" in email_arribado.subject
    assert t_espera >= 0.25, f"Debe haber esperado al menos 0.25s, demoró {t_espera:.2f}s"

    # B. Búsqueda REST vía search_messages
    res_busqueda = await mailpit_client.search_messages(query="Cuota Comercial")
    assert res_busqueda.total_found == 1
    assert len(res_busqueda.messages) == 1
    assert "Aviso de Cuota" in res_busqueda.messages[0].subject

    # C. Búsqueda con 0 coincidencias
    res_vacia = await mailpit_client.search_messages(query="consulta_inexistente_xyz_123")
    assert res_vacia.total_found == 0
    assert len(res_vacia.messages) == 0

    # D. Timeout controlado cuando el correo nunca arriba
    with pytest.raises(TimeoutError, match="No se recibió correo"):
        await mailpit_client.wait_for_email(
            subject_contains="Correo Imposible Que Nunca Llegará",
            timeout_sec=0.4,
        )


# ==============================================================================
# 4. test_purga_atomica_buzon_mailpit
# ==============================================================================


@pytest.mark.asyncio
async def test_purga_atomica_buzon_mailpit(
    mailpit_client: MailpitClient,
) -> None:
    """Certifica que delete_all_messages vacía completamente el buzón."""
    # 1. Verificar estado inicial limpio
    mensajes_iniciales = await mailpit_client.get_messages()
    assert len(mensajes_iniciales) == 0

    # 2. Inyectar correos sintéticos
    for idx in range(3):
        await send_email_smtp(
            subject=f"Notificación en Ráfaga {idx}",
            sender="ci-bot@kallicode.dev",
            recipients=[f"dev{idx}@kallicode.dev"],
            text_body=f"Cuerpo {idx}",
        )

    mensajes_cargados = await mailpit_client.get_messages()
    assert len(mensajes_cargados) == 3

    # 3. Purgar atómicamente
    resultado_purga = await mailpit_client.delete_all_messages()
    assert resultado_purga is True

    # 4. Verificar estado final completamente vacío
    mensajes_post_purga = await mailpit_client.get_messages()
    assert len(mensajes_post_purga) == 0


# ==============================================================================
# 5. test_inmutabilidad_dto_captured_email
# ==============================================================================


def test_inmutabilidad_dto_captured_email() -> None:
    """Garantiza que CapturedEmailDTO y MailpitSearchSummaryDTO sean inmutables (frozen=True)."""
    email = CapturedEmailDTO(
        message_id="msg_mailpit_12345",
        sender="noreply@kallicode.dev",
        recipients=["dev@kallicode.dev"],
        subject="Reporte de Calidad CI/CD",
        text_body="Suite finalizada exitosamente.",
        html_body="<p>Suite finalizada <b>100% OK</b>.</p>",
        headers={"X-KalliCode-Tenant-ID": "tenant-test"},
        created_at="2026-11-08T22:00:00Z",
    )

    with pytest.raises(ValidationError):
        # Mutación prohibida por frozen=True
        email.subject = "Asunto Alterado Ilegalmente"

    with pytest.raises(ValidationError):
        email.recipients = ["hacker@evil.com"]

    summary = MailpitSearchSummaryDTO(
        total_found=1,
        messages=[email],
    )

    with pytest.raises(ValidationError):
        summary.total_found = 999


# ==============================================================================
# 6. test_context_manager_mailpit_client
# ==============================================================================


@pytest.mark.asyncio
async def test_context_manager_mailpit_client() -> None:
    """Verifica que MailpitClient opere de forma segura como context manager asíncrono y con cliente externo."""
    async with MailpitClient() as client:
        res = await client.get_messages()
        assert isinstance(res, list)

    # Inyección de cliente externo httpx
    async with httpx.AsyncClient(timeout=5.0) as external_http:
        client_externo = MailpitClient(http_client=external_http)
        mensajes = await client_externo.get_messages()
        assert isinstance(mensajes, list)
        await client_externo.aclose()
