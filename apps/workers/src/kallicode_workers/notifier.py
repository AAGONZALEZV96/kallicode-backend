"""Notificador de correo por SMTP genérico (D11).

Consume kc:correo. Plantillas mínimas en español (TODO: catálogo es/en/pt
por idioma del usuario). En local, Mailpit (localhost:1025) captura todo.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

import anyio

# Importamos la bóveda para recuperar secretos de forma segura
from kallicode_api.servicios import boveda
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.logging import hash_email, log
from sqlalchemy import text

_PLANTILLAS = {
    "invitacion": (
        "Te invitaron a Kallicode",
        "Has sido invitado al portal Kallicode.\n"
        "Completa tu alta con este token: {token}\n\n{mensaje}",
    ),
    "reset_password": (
        "Restablece tu contraseña de Kallicode",
        "Usa este token para restablecer tu contraseña (válido 30 minutos): {token}",
    ),
}


def _enviar_smtp(destino: str, asunto: str, cuerpo: str) -> None:
    s = get_settings()
    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, s.smtp_from, destino
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_tls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def procesar(campos: dict) -> None:
    """Entrada (stream): {tipo, email|user_id, token_ref?, tenant_id?, mensaje?, token? (legado)}."""
    tipo = campos.get("tipo", "")
    email = campos.get("email")
    if not email and campos.get("user_id"):
        async with sesion_sistema() as db:
            u = await uno(
                db,
                "SELECT email FROM core.users WHERE id = :i",
                {"i": campos["user_id"]},
            )
            email = u and u["email"]
    if not email or tipo not in _PLANTILLAS:
        log.warning("notifier.descartado", tipo=tipo)
        return

    # --- INICIO MODIFICACIÓN BE-F05 ---
    # Soporte legado por si hay mensajes antiguos en la cola
    token = campos.get("token", "")

    token_ref = campos.get("token_ref")
    tenant_id = campos.get("tenant_id")

    # Recuperación segura "Just-In-Time" desde la bóveda
    if token_ref and tenant_id:
        secreto = await boveda.leer(tenant_id, token_ref)
        if secreto and "token" in secreto:
            token = secreto["token"]
        else:
            log.error("notifier.token_faltante", ref=token_ref)
            return
    # --- FIN MODIFICACIÓN ---

    asunto, cuerpo = _PLANTILLAS[tipo]
    cuerpo = cuerpo.format(token=token, mensaje=campos.get("mensaje", ""))

    try:
        await anyio.to_thread.run_sync(_enviar_smtp, email, asunto, cuerpo)
        log.info("notifier.enviado", tipo=tipo, email=hash_email(email))

        # Higiene de seguridad: Eliminamos el secreto una vez consumido con éxito
        if token_ref and tenant_id:
            await boveda.eliminar(tenant_id, token_ref)

    except Exception:
        log.error("notifier.fallo", tipo=tipo, email=hash_email(email), exc_info=True)


async def avisar_comercial() -> None:
    """Las solicitudes registradas siguen pendientes hasta que SMTP acepta el aviso."""
    destino = get_settings().comercial_email
    if not destino:
        return
    async with sesion_sistema() as db:
        pendientes = await todos(
            db,
            """SELECT id, tenant_id FROM core.upgrade_requests
               WHERE estado = 'registrada' ORDER BY creado_en, id""",
        )
    for pendiente in pendientes:
        tenant, solicitud_id = pendiente["tenant_id"], pendiente["id"]
        try:
            async with sesion_tenant(tenant, actor="svc:comercial") as db:
                solicitud = await uno(
                    db,
                    """SELECT plan_deseado, urgente FROM core.upgrade_requests
                       WHERE tenant_id = :t AND id = :id AND estado = 'registrada'
                       FOR UPDATE SKIP LOCKED""",
                    {"t": tenant, "id": solicitud_id},
                )
                if not solicitud:
                    continue
                asunto = "Solicitud urgente de mejora" if solicitud["urgente"] else "Mejora de plan"
                await anyio.to_thread.run_sync(
                    _enviar_smtp,
                    destino,
                    asunto,
                    f"Organización: {tenant}\nSolicitud: {solicitud_id}\n"
                    f"Plan solicitado: {solicitud['plan_deseado']}",
                )
                await db.execute(
                    text("""
                        UPDATE core.upgrade_requests SET estado = 'en_gestion'
                        WHERE tenant_id = :t AND id = :id
                    """),
                    {"t": tenant, "id": solicitud_id},
                )
        except Exception:
            log.exception("notifier.mejora_fallida", solicitud_id=solicitud_id)
