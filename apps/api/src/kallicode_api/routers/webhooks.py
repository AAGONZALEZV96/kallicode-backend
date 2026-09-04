"""Webhooks de integraciones entrantes (§16 del diseño).

Patrón inbox común: (1) verificación HMAC-SHA256 con el secreto de la
conexión — sin firma válida, 401 con cuerpo vacío deliberado; (2) 202
inmediato tras persistir el payload crudo en core.webhook_inbox; (3) el
worker normalizer lo convierte a ticket; (4) idempotencia por delivery_id.
El tenant se resuelve por el token de ruta (wh_...), nunca por el cuerpo.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import text

from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.errors import AppError
from kallicode_core.logging import log

from ..servicios import boveda

router = APIRouter(tags=["Webhooks"])

_MAX_PAYLOAD = 1_048_576  # 1 MB


async def _recibir(proveedor: str, token: str, request: Request,
                   firma: str | None, delivery_id: str | None,
                   evento: str | None = None) -> dict:
    """Núcleo del patrón inbox. Devuelve la fila insertada (o existente).

    Errores: FIRMA_INVALIDA (401, cuerpo mínimo) · PAYLOAD_EXCESIVO (413) ·
    RATE_LIMIT (429 con Retry-After, RL-3).
    Log: webhooks.<proveedor>.recibido / firma_invalida / duplicado.
    """
    cuerpo = await request.body()
    if len(cuerpo) > _MAX_PAYLOAD:
        raise AppError("PAYLOAD_EXCESIVO", 413, "Payload demasiado grande.")

    async with sesion_sistema() as db:
        cn = await uno(db, """SELECT * FROM core.connections
                               WHERE ruta_token = :rt AND estado = 'conectada'""",
                       {"rt": token})
    if not cn:
        log.warning("webhooks.firma_invalida", proveedor=proveedor, motivo="token")
        raise AppError("FIRMA_INVALIDA", 401, "")
    await comercial.rl_webhook(cn["id"])

    secreto = await boveda.leer(f"cn--{cn['id']}") or {}
    clave = (secreto.get("webhook_secret") or secreto.get("api_token")
             or secreto.get("token") or "")
    esperada = hmac.new(clave.encode(), cuerpo, hashlib.sha256).hexdigest()
    recibida = (firma or "").removeprefix("sha256=")
    if not clave or not hmac.compare_digest(esperada, recibida):
        log.warning("webhooks.firma_invalida", proveedor=proveedor,
                    connection_id=cn["id"])
        raise AppError("FIRMA_INVALIDA", 401, "")

    try:
        payload = json.loads(cuerpo)
    except json.JSONDecodeError:
        payload = {"_crudo": cuerpo.decode(errors="replace")}

    async with sesion_sistema() as db:
        fila = await uno(db, """
            INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id,
                                            evento, payload)
            VALUES (:t, :c, :d, :e, CAST(:p AS jsonb))
            ON CONFLICT (connection_id, delivery_id) DO NOTHING
            RETURNING id""",
            {"t": cn["tenant_id"], "c": cn["id"],
             "d": delivery_id or hashlib.sha256(cuerpo).hexdigest()[:32],
             "e": evento, "p": json.dumps(payload)})
    if fila is None:
        log.info("webhooks.duplicado", proveedor=proveedor, delivery_id=delivery_id)
    else:
        log.info(f"webhooks.{proveedor}.recibido", delivery_id=delivery_id,
                 evento=evento, connection_id=cn["id"])
        await comercial.redis_cliente().xadd("kc:inbox", {"inbox_id": str(fila["id"])})
    return {"recibido": True, "tenant_id": cn["tenant_id"], "inbox_id":
            fila and fila["id"]}


@router.post("/jira/{token}", status_code=202, summary="Webhook de Jira")
async def jira(token: str, request: Request,
               x_hub_signature: str | None = Header(default=None),
               x_atlassian_webhook_identifier: str | None = Header(default=None)) -> dict:
    """Eventos jira:issue_created/updated. El normalizador mapea summary→
    titulo, description (ADF)→descripcion, priority→prioridad."""
    r = await _recibir("jira", token, request, x_hub_signature,
                       x_atlassian_webhook_identifier, "jira:issue")
    return {"recibido": r["recibido"]}


@router.post("/github/{token}", status_code=202, summary="Webhook de GitHub Issues")
async def github(token: str, request: Request,
                 x_hub_signature_256: str | None = Header(default=None),
                 x_github_delivery: str | None = Header(default=None),
                 x_github_event: str | None = Header(default=None)) -> dict:
    """Solo eventos issues; otros se aceptan con 202 y se ignoran registrados."""
    r = await _recibir("github", token, request, x_hub_signature_256,
                       x_github_delivery, x_github_event)
    return {"recibido": r["recibido"]}


@router.post("/kallicode-help/{token}", status_code=202,
             summary="Webhook de Kallicode Help")
async def kallicode_help(token: str, request: Request, response: Response,
                         x_kc_signature: str | None = Header(default=None),
                         x_kc_delivery: str | None = Header(default=None)) -> dict:
    """Fuente más rica: diagnóstico + evidencia + usuario final. Normalización
    síncrona best-effort (<2 s) para devolver ticket_id a Help; si no llega a
    tiempo, se comunica después por el canal de vuelta.
    Comercial: disponible desde el plan Growth (FUNCION_NO_DISPONIBLE en Starter)."""
    r = await _recibir("help", token, request, x_kc_signature, x_kc_delivery,
                       "help:ticket")
    async with sesion_sistema() as db:
        plan = await uno(db, """SELECT p.permite_help FROM core.subscriptions s
                                 JOIN core.plans p ON p.codigo = s.plan_codigo
                                WHERE s.tenant_id = :t AND s.finaliza_el IS NULL""",
                         {"t": r["tenant_id"]})
    if plan and not plan["permite_help"]:
        raise AppError("FUNCION_NO_DISPONIBLE", 403,
                       "Kallicode Help no está incluido en el plan del tenant.")
    # normalización síncrona best-effort la hace el worker con prioridad;
    # aquí devolvemos sin ticket_id si aún no está (TODO: espera corta opcional)
    return {"recibido": True, "ticket_id": None}


@router.post("/corestream/{token}", status_code=202, summary="Webhook de Corestream")
async def corestream(token: str, request: Request,
                     x_cs_signature: str | None = Header(default=None),
                     x_cs_delivery: str | None = Header(default=None)) -> dict:
    r = await _recibir("corestream", token, request, x_cs_signature, x_cs_delivery,
                       "cs:ticket")
    return {"recibido": r["recibido"]}


@router.post("/ci/{token}", status_code=202, summary="Callback de CI/CD")
async def ci(token: str, request: Request,
             x_hub_signature_256: str | None = Header(default=None),
             x_github_delivery: str | None = Header(default=None)) -> dict:
    """Resultados de workflows del pipeline del cliente; el worker correlaciona
    por run_id con el job. run_id sin job se registra y descarta con 202."""
    r = await _recibir("ci", token, request, x_hub_signature_256,
                       x_github_delivery, "ci:resultado")
    return {"recibido": r["recibido"]}
