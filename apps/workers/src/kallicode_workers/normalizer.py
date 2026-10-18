"""Normalizador: webhook_inbox → ticket interno (§16 del diseño).

procesar_inbox: convierte el payload crudo de cada proveedor al formato único
de ticket. Errores de normalización NUNCA vuelven al proveedor: quedan en la
inbox con estado 'fallido' y ERROR en el log central.

KC-BE-04-2: este módulo ya NO ejecuta el LLM. Solo normaliza y crea el
ticket en `core.tickets`. El triage completo (classify, dedup, impact)
lo ejecuta el orquestador sobre `core.jobs` (ver
apps/workers/src/kallicode_workers/orquestador/etapas.py).
"""

from __future__ import annotations

import json

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import cuota_agotada
from kallicode_core.db import sesion_sistema, sesion_tenant, uno
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from sqlalchemy import text


# ------------------------------------------------------------- normalización
def _de_jira(p: dict) -> dict | None:
    issue = p.get("issue") or {}
    campos = issue.get("fields") or {}
    if p.get("webhookEvent") not in ("jira:issue_created", "jira:issue_updated"):
        return None
    prioridad = {
        "highest": "alta",
        "high": "alta",
        "medium": "media",
        "low": "baja",
        "lowest": "baja",
    }.get(str((campos.get("priority") or {}).get("name", "")).lower(), "media")
    return {
        "tipo": "bug",
        "titulo": campos.get("summary") or issue.get("key", "Jira"),
        "descripcion": json.dumps(campos.get("description") or "")[:10000] or "-",
        "prioridad": prioridad,
        "clave_externa": issue.get("key"),
    }


def _de_github(p: dict) -> dict | None:
    if p.get("action") not in ("opened", "edited", "labeled"):
        return None
    issue = p.get("issue") or {}
    etiquetas = {e.get("name") for e in issue.get("labels", [])}
    if "kallicode:skip" in etiquetas:
        return None
    tipo = "mejora" if "enhancement" in etiquetas else "bug"
    return {
        "tipo": tipo,
        "titulo": issue.get("title", "GitHub issue"),
        "descripcion": (issue.get("body") or "-")[:10000],
        "prioridad": "media",
        "clave_externa": str(issue.get("number")),
    }


def _de_help(p: dict) -> dict | None:
    tipo = {"bug": "bug", "mejora": "mejora", "requerimiento": "funcionalidad"}.get(
        p.get("tipo", "bug"), "bug"
    )
    return {
        "tipo": tipo,
        "titulo": p.get("titulo", "Kallicode Help"),
        "descripcion": (p.get("descripcion") or "-")[:10000],
        "prioridad": ("alta" if p.get("diagnostico", {}).get("confianza", 0) > 0.8 else "media"),
        "usuario_final": p.get("usuario_final"),
        "diagnostico": p.get("diagnostico"),
    }


def _de_corestream(p: dict) -> dict | None:
    tk = p.get("ticket") or {}
    tipo = {"bug": "bug", "improvement": "mejora"}.get(tk.get("kind", "bug"), "bug")
    return {
        "tipo": tipo,
        "titulo": tk.get("title", "Corestream"),
        "descripcion": (tk.get("body") or "-")[:10000],
        "prioridad": {"high": "alta", "low": "baja"}.get(tk.get("priority"), "media"),
        "clave_externa": tk.get("id"),
    }


_NORMALIZADORES = {
    "jira:issue": _de_jira,
    "issues": _de_github,
    "help:ticket": _de_help,
    "cs:ticket": _de_corestream,
    "ci:resultado": None,
}


async def procesar_inbox(campos: dict) -> None:
    """Consume kc:inbox: normaliza una entrada de webhook_inbox a ticket."""
    inbox_id = int(campos["inbox_id"])
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            "SELECT * FROM core.webhook_inbox WHERE id=:i AND estado='pendiente' FOR UPDATE SKIP LOCKED",
            {"i": inbox_id},
        )
        if not fila:
            return
        if fila["evento"] == "ci:resultado":
            # correlación con el job por run_id (TODO produccion: señal al orquestador)
            await db.execute(
                text(
                    "UPDATE core.webhook_inbox SET estado='procesado', procesado_en=now() WHERE id=:i"
                ),
                {"i": inbox_id},
            )
            log.info("webhooks.ci.resultado", inbox_id=inbox_id)
            return
        normalizador = _NORMALIZADORES.get(fila["evento"] or "")
        try:
            ticket = normalizador(fila["payload"]) if normalizador else None
        except Exception:
            ticket = None
            log.error("webhooks.normalizacion", inbox_id=inbox_id, exc_info=True)
        if ticket is None:
            estado = "ignorado" if normalizador else "fallido"
            await db.execute(
                text("UPDATE core.webhook_inbox SET estado=:e, procesado_en=now() WHERE id=:i"),
                {"e": estado, "i": inbox_id},
            )
            return
        tenant = fila["tenant_id"]
    # crea el ticket dentro del tenant (QU-1: cola por cuota)
    async with sesion_tenant(tenant, actor="svc:normalizer") as db:
        origen = {
            "jira:issue": "jira",
            "issues": "github",
            "help:ticket": "kallicode_help",
            "cs:ticket": "corestream",
        }[fila["evento"]]
        pausado = await cuota_agotada(db, tenant)
        tid = nuevo("tk")
        creado = await uno(
            db,
            """
            INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion,
                                      prioridad, etapa, origen, origen_ref, usuario_final)
            VALUES (:i, :t, :tipo, :ti, :d, :p, :e, :o, CAST(:oref AS jsonb),
                    CAST(:uf AS jsonb))
            RETURNING numero""",
            {
                "i": tid,
                "t": tenant,
                "tipo": ticket["tipo"],
                "ti": ticket["titulo"][:200],
                "d": ticket["descripcion"],
                "p": ticket["prioridad"],
                "e": "en_cola_por_cuota" if pausado else "triage",
                "o": origen,
                "oref": json.dumps(
                    {
                        "clave_externa": ticket.get("clave_externa"),
                        "diagnostico": ticket.get("diagnostico"),
                    }
                ),
                "uf": (
                    json.dumps(ticket.get("usuario_final")) if ticket.get("usuario_final") else None
                ),
            },
        )
        await db.execute(
            text(
                """UPDATE core.webhook_inbox SET estado='procesado',
                                        procesado_en=now(), ticket_id=:tk WHERE id=:i"""
            ),
            {"tk": tid, "i": inbox_id},
        )
        await registrar_evento(
            db,
            tenant,
            evento="ticket_creado",
            resumen=f"Ticket creado desde {origen}",
            actor_tipo="sistema",
            ticket_id=creado["numero"],
        )
    # KC-BE-04-2: no se encola a kc:triage. El orquestador consulta core.jobs
    # directamente cada 2 segundos. El ticket queda en 'triage' y el
    # orquestador lo recogerá en el siguiente ciclo.
    log.info(
        "normalizer.ticket",
        inbox_id=inbox_id,
        ticket_id=creado["numero"],
        origen=origen,
        encolado_por_cuota=pausado,
    )


# NOTA: `procesar_triage` se ha eliminado en KC-BE-04-2. El triage completo
# (classify + dedup + impact) lo ejecuta el orquestador sobre core.jobs
# (ver apps/workers/src/kallicode_workers/orquestador/etapas.py).
