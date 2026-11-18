"""Webhooks de integraciones entrantes (§16 del diseño, endurecido en KC-BE-07-2).

Patrón inbox común:
(1) Validación de tamaño de payload por proveedor (413 PAYLOAD_EXCESIVO);
(2) Resolución de conexión por token de ruta (wh_...) y rate limit por conexión
    (429 RATE_LIMIT_EXCEDIDO con Retry-After);
(3) Verificación de ventana temporal anti-replay y firma HMAC-SHA256 con soporte
    de rotación de secreto (convivencia de secreto actual + anterior vigente) —
    ante fallo de firma o marca temporal fuera de ventana, 401 de cuerpo mínimo;
(4) 202 inmediato tras persistir en core.webhook_inbox con unicidad por
    (connection_id, delivery_id) y publicar en kc:inbox.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Header, Request, Response
from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.errors import AppError
from kallicode_core.logging import log

from ..servicios import boveda, firmas

router = APIRouter(tags=["Webhooks"])


def _extraer_timestamp_header(request: Request, explicito: str | None = None) -> str | None:
    if explicito:
        return explicito
    for cabecera in (
        "x-webhook-timestamp",
        "x-hub-timestamp",
        "x-gitlab-timestamp",
        "x-atlassian-webhook-timestamp",
        "x-kc-timestamp",
        "x-cs-timestamp",
    ):
        valor = request.headers.get(cabecera)
        if valor:
            return valor
    return None


async def _verificar_firma_y_conexion(
    proveedor: str,
    token: str,
    request: Request,
    firma: str | None,
    timestamp_header: str | None = None,
) -> tuple[dict, bytes, dict]:
    """Fase 1 (SOLO LECTURA): límite por proveedor, conexión, rate limit, anti-replay y firma."""
    cuerpo = await request.body()
    firmas.validar_tamano_payload(proveedor, cuerpo)

    async with sesion_sistema() as db:
        cn = await uno(
            db,
            """SELECT * FROM core.connections
                               WHERE ruta_token = :rt AND estado = 'conectada'""",
            {"rt": token},
        )
    if not cn:
        log.warning("webhooks.firma_invalida", proveedor=proveedor, motivo="token")
        raise AppError("FIRMA_INVALIDA", 401, "")

    await comercial.rl_webhook(cn["tenant_id"], cn["id"])

    secreto_boveda = await boveda.leer(cn["tenant_id"], f"cn--{cn['id']}") or {}
    secretos_vigentes = firmas.extraer_secretos_vigentes(secreto_boveda)

    # Fallback temporal por migración legado si la bóveda aún no tiene secreto
    if not secretos_vigentes:
        legado = (cn.get("configuracion") or {}).get("webhook_secret") or ""
        if legado:
            log.warning("webhooks.fallback_legado", connection_id=cn["id"])
            secretos_vigentes = [str(legado)]

    ts_raw = _extraer_timestamp_header(request, timestamp_header)
    firmas.verificar_firma_entrega(
        proveedor=proveedor,
        cuerpo=cuerpo,
        firma_header=firma,
        timestamp_header=ts_raw,
        secretos_vigentes=secretos_vigentes,
    )

    try:
        payload = json.loads(cuerpo)
    except json.JSONDecodeError:
        payload = {"_crudo": cuerpo.decode(errors="replace")}

    return cn, cuerpo, payload


async def _persistir_y_publicar(
    proveedor: str,
    cn: dict,
    cuerpo: bytes,
    payload: dict,
    delivery_id: str | None,
    evento: str | None,
) -> int | None:
    """Fase 2 (ESCRITURA): persistir en webhook_inbox y publicar en kc:inbox.

    Idempotente por (connection_id, delivery_id) dentro de la partición mensual.
    """
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id,
                                            evento, payload)
            VALUES (:t, :c, :d, :e, CAST(:p AS jsonb))
            ON CONFLICT DO NOTHING
            RETURNING id""",
            {
                "t": cn["tenant_id"],
                "c": cn["id"],
                "d": delivery_id or hashlib.sha256(cuerpo).hexdigest()[:32],
                "e": evento,
                "p": json.dumps(payload),
            },
        )
    if fila is None:
        log.info("webhooks.duplicado", proveedor=proveedor, delivery_id=delivery_id)
        return None

    log.info(
        f"webhooks.{proveedor}.recibido",
        delivery_id=delivery_id,
        inbox_id=fila["id"],
        tenant_id=cn["tenant_id"],
    )
    from kallicode_core.comercial import redis_cliente

    await redis_cliente().xadd(
        "kc:inbox",
        {
            "inbox_id": str(fila["id"]),
            "tenant_id": cn["tenant_id"],
            "connection_id": cn["id"],
            "proveedor": proveedor,
        },
        maxlen=50_000,
        approximate=True,
    )
    return fila["id"]


async def _recibir(
    proveedor: str,
    token: str,
    request: Request,
    firma: str | None,
    delivery_id: str | None,
    evento: str | None,
    timestamp_header: str | None = None,
) -> dict | Response:
    try:
        cn, cuerpo, payload = await _verificar_firma_y_conexion(
            proveedor,
            token,
            request,
            firma,
            timestamp_header=timestamp_header,
        )
    except AppError as exc:
        if exc.codigo == "FIRMA_INVALIDA":
            return Response(status_code=401)
        raise

    inbox_id = await _persistir_y_publicar(proveedor, cn, cuerpo, payload, delivery_id, evento)
    return {"recibido": True, "inbox_id": inbox_id}


@router.post("/github/{token}", status_code=202, summary="Webhook de GitHub")
async def github(
    token: str,
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    r = await _recibir(
        "github",
        token,
        request,
        x_hub_signature_256,
        x_github_delivery,
        x_github_event,
        timestamp_header=x_webhook_timestamp,
    )
    if isinstance(r, Response):
        return r
    return {"recibido": r["recibido"]}


@router.post("/gitlab/{token}", status_code=202, summary="Webhook de GitLab")
async def gitlab(
    token: str,
    request: Request,
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event_uuid: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    r = await _recibir(
        "gitlab",
        token,
        request,
        x_gitlab_token,
        x_gitlab_event_uuid,
        x_gitlab_event,
        timestamp_header=x_webhook_timestamp,
    )
    if isinstance(r, Response):
        return r
    return {"recibido": r["recibido"]}


@router.post("/jira/{token}", status_code=202, summary="Webhook de Jira")
async def jira(
    token: str,
    request: Request,
    x_hub_signature: str | None = Header(default=None),
    x_atlassian_webhook_identifier: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    r = await _recibir(
        "jira",
        token,
        request,
        x_hub_signature,
        x_atlassian_webhook_identifier,
        "jira:issue",
        timestamp_header=x_webhook_timestamp,
    )
    if isinstance(r, Response):
        return r
    return {"recibido": r["recibido"]}


@router.post("/help/{token}", status_code=202, summary="Webhook de Kallicode Help")
@router.post(
    "/kallicode-help/{token}", status_code=202, summary="Webhook de Kallicode Help (alias)"
)
async def help_wh(
    token: str,
    request: Request,
    x_kc_signature: str | None = Header(default=None),
    x_kc_delivery: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    """Kallicode Help está en el plan (Business+); Starter recibe 403
    FUNCION_NO_DISPONIBLE. Si está incluido, los tickets entran con prioridad
    y SLA reales (KC-BE-04-6: plan verificado ANTES de persistir)."""
    try:
        cn, cuerpo, payload = await _verificar_firma_y_conexion(
            "help",
            token,
            request,
            x_kc_signature,
            timestamp_header=x_webhook_timestamp,
        )
    except AppError as exc:
        if exc.codigo == "FIRMA_INVALIDA":
            return Response(status_code=401)
        raise

    async with sesion_sistema() as db:
        plan = await comercial.plan_vigente(db, cn["tenant_id"])
    if not plan or not plan["permite_help"]:
        raise AppError(
            "FUNCION_NO_DISPONIBLE",
            403,
            "Kallicode Help no está incluido en tu plan.",
        )
    await _persistir_y_publicar("help", cn, cuerpo, payload, x_kc_delivery, "help:ticket")
    return {"recibido": True, "ticket_id": None}


@router.post("/corestream/{token}", status_code=202, summary="Webhook de Corestream")
async def corestream(
    token: str,
    request: Request,
    x_cs_signature: str | None = Header(default=None),
    x_cs_delivery: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    r = await _recibir(
        "corestream",
        token,
        request,
        x_cs_signature,
        x_cs_delivery,
        "cs:ticket",
        timestamp_header=x_webhook_timestamp,
    )
    if isinstance(r, Response):
        return r
    return {"recibido": r["recibido"]}


@router.post("/ci/{token}", status_code=202, summary="Callback de CI/CD")
async def ci(
    token: str,
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    """Resultados de workflows del pipeline del cliente; el worker correlaciona
    por run_id con el job. run_id sin job se registra y descarta con 202."""
    r = await _recibir(
        "ci",
        token,
        request,
        x_hub_signature_256,
        x_github_delivery,
        "ci:resultado",
        timestamp_header=x_webhook_timestamp,
    )
    if isinstance(r, Response):
        return r
    return {"recibido": r["recibido"]}
