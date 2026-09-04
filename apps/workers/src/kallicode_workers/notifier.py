"""Notificador de correo por SMTP genérico (D11).

Consume kc:correo. Plantillas mínimas en español (TODO: catálogo es/en/pt
por idioma del usuario). En local, Mailpit (localhost:1025) captura todo.
"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

import anyio

from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.logging import hash_email, log

_PLANTILLAS = {
    "invitacion": ("Te invitaron a Kallicode",
                   "Has sido invitado al portal Kallicode.\n"
                   "Completa tu alta con este token: {token}\n\n{mensaje}"),
    "reset_password": ("Restablece tu contraseña de Kallicode",
                       "Usa este token para restablecer tu contraseña "
                       "(válido 30 minutos): {token}"),
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
    """Entrada (stream): {tipo, email|user_id, token?, mensaje?}."""
    tipo = campos.get("tipo", "")
    email = campos.get("email")
    if not email and campos.get("user_id"):
        async with sesion_sistema() as db:
            u = await uno(db, "SELECT email FROM core.users WHERE id = :i",
                          {"i": campos["user_id"]})
            email = u and u["email"]
    if not email or tipo not in _PLANTILLAS:
        log.warning("notifier.descartado", tipo=tipo)
        return
    asunto, cuerpo = _PLANTILLAS[tipo]
    cuerpo = cuerpo.format(token=campos.get("token", ""),
                           mensaje=campos.get("mensaje", ""))
    try:
        await anyio.to_thread.run_sync(_enviar_smtp, email, asunto, cuerpo)
        log.info("notifier.enviado", tipo=tipo, email=hash_email(email))
    except Exception:
        log.error("notifier.fallo", tipo=tipo, email=hash_email(email), exc_info=True)
