r"""Normalizador: webhook_inbox → ticket interno + job de triage.

procesar_inbox: convierte el payload crudo de cada proveedor al formato único
de ticket. Errores de normalización NUNCA vuelven al proveedor: quedan en la
inbox con estado 'fallido' y ERROR en el log central.

KC-BE-04-2: este módulo ya NO ejecuta el LLM. Solo normaliza, crea el
ticket en `core.tickets` y **crea el job en `core.jobs`** con estado
'pendiente_asignacion' para que el orquestador lo reserve y ejecute el
triage (classify, dedup, impact) sobre él. Sin este INSERT, el ticket se
queda huérfano y el pipeline no arranca desde el webhook.

KC-BE-04-4 (cierra BE-F17): la toma del inbox y la creación del ticket
son convergentes sin necesitar un estado intermedio. El estado 'procesando'
del plan original NO existe en `wi_estado_chk` (verificado con \d), así que
la convergencia se hace en dos fases:

  T1 (sesion_sistema, solo lectura): lee tenant_id, evento y payload de la
      fila si sigue en 'pendiente'. No bloquea ni escribe nada.
  T2 (sesion_tenant, escritura): SELECT ... FOR UPDATE; si la fila ya está
      'procesado' o tiene ticket_id, sale sin crear nada. Si sigue
      'pendiente', inserta el ticket, inserta el job, marca 'procesado'
      con ticket_id y registra auditoría, todo en la misma transacción
      del tenant.

Dos réplicas concurrentes: ambas pasan T1; el FOR UPDATE de T2 serializa;
la primera hace COMMIT con estado='procesado'; la segunda lee 'procesado'
y sale. Exactamente un ticket y un job. No requiere ALTER ni cambios en /db/.
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

_ORIGENES = {
    "jira:issue": "jira",
    "issues": "github",
    "help:ticket": "kallicode_help",
    "cs:ticket": "corestream",
}


async def procesar_inbox(campos: dict) -> None:
    """
    Consume kc:inbox: normaliza una entrada de webhook_inbox a ticket y job.

    Convergencia (KC-BE-04-4): T1 lee sin bloquear; T2 hace SELECT ... FOR
    UPDATE y solo crea el ticket si la fila sigue 'pendiente'.

    Creación del job (KC-BE-04-2): tras insertar el ticket, se inserta el
    job en `core.jobs` con estado 'pendiente_asignacion'. El orquestador
    consulta esa tabla cada 2 s y lo reserva.
    """
    inbox_id = int(campos["inbox_id"])
    # ---- T1: leer tenant/evento/payload (sin bloquear) ----
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT tenant_id, evento, payload
            FROM core.webhook_inbox
            WHERE id = :i AND estado = 'pendiente'
            """,
            {"i": inbox_id},
        )
        if not fila:
            log.debug("webhooks.inbox_no_pendiente", inbox_id=inbox_id)
            return

    tenant = fila["tenant_id"]
    evento = fila["evento"]

    # ---- Caso especial: resultado de CI (no genera ticket) ----
    if evento == "ci:resultado":
        async with sesion_tenant(tenant, actor="svc:normalizer") as db:
            await db.execute(
                text("""
                    UPDATE core.webhook_inbox
                    SET estado='procesado', procesado_en=now()
                    WHERE id=:i AND estado='pendiente'
                """),
                {"i": inbox_id},
            )
        log.info("webhooks.ci.resultado", inbox_id=inbox_id)
        return

    normalizador = _NORMALIZADORES.get(evento or "")
    try:
        ticket = normalizador(fila["payload"]) if normalizador else None
    except Exception:
        ticket = None
        log.error("webhooks.normalizacion", inbox_id=inbox_id, exc_info=True)
    # ---- T2: transacción del tenant (idempotente por FOR UPDATE) ----
    async with sesion_tenant(tenant, actor="svc:normalizer") as db:
        fila_actual = await uno(
            db,
            """
            SELECT estado, ticket_id
            FROM core.webhook_inbox
            WHERE id = :i
            FOR UPDATE
            """,
            {"i": inbox_id},
        )
        if not fila_actual:
            log.warning("webhooks.inbox_desaparecida", inbox_id=inbox_id)
            return
        if fila_actual["ticket_id"] is not None:
            log.info(
                "webhooks.inbox_ya_procesada",
                inbox_id=inbox_id,
                ticket_id=fila_actual["ticket_id"],
            )
            return
        if fila_actual["estado"] != "pendiente":
            log.warning(
                "webhooks.inbox_estado_inesperado",
                inbox_id=inbox_id,
                estado=fila_actual["estado"],
            )
            return

        if ticket is None:
            estado = "ignorado" if normalizador else "fallido"
            await db.execute(
                text("""
                    UPDATE core.webhook_inbox
                    SET estado=:e, procesado_en=now()
                    WHERE id=:i
                """),
                {"e": estado, "i": inbox_id},
            )
            return

        origen = _ORIGENES.get(evento)
        if origen is None:
            log.error("webhooks.evento_sin_origen", inbox_id=inbox_id, evento=evento)
            await db.execute(
                text("""
                    UPDATE core.webhook_inbox
                    SET estado='fallido', procesado_en=now()
                    WHERE id=:i
                """),
                {"i": inbox_id},
            )
            return

        pausado = await cuota_agotada(db, tenant)
        tid = nuevo("tk")

        # ---- Crear el ticket ----
        creado = await uno(
            db,
            """
            INSERT INTO core.tickets (
                id, tenant_id, tipo, titulo, descripcion,
                prioridad, etapa, origen, origen_ref, usuario_final
            )
            VALUES (
                :i, :t, :tipo, :ti, :d, :p, :e, :o,
                CAST(:oref AS jsonb), CAST(:uf AS jsonb)
            )
            RETURNING id, numero
            """,
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
                    json.dumps(ticket["usuario_final"]) if ticket.get("usuario_final") else None
                ),
            },
        )

        # NUEVO (KC-BE-04-2): crear el job de triage.
        # El orquestador consulta core.jobs cada 2 s buscando jobs con
        # estado='pendiente_asignacion'. Sin este INSERT, el ticket se
        # queda huérfano y el pipeline no arranca desde el webhook.
        # 🔴 NUEVO (KC-BE-04-2): crear el job de triage.
        #  - prioridad: el ticket usa alta/media/baja; el job exige P1..P4
        #    (CHECK job_prio_chk). Mapeamos.
        #  - estado: si el ticket está pausado por cuota, el job nace en
        #    'pausado_por_cuota' para que el orquestador NO lo procese
        #    hasta que se renueve el ciclo.
        _PRIORIDAD_TICKET_A_JOB = {
            "alta": "P1",
            "media": "P2",
            "baja": "P3",
        }
        estado_inicial = "pausado_por_cuota" if pausado else "pendiente_asignacion"

        trabajo_id = nuevo("jb")
        await uno(
            db,
            """
            INSERT INTO core.jobs (
                id, tenant_id, ticket_id, estado, prioridad, contexto
            )
            VALUES (
                :i, :t, :tk, :e, :p, CAST(:ctx AS jsonb)
            )
            RETURNING id
            """,
            {
                "i": trabajo_id,
                "t": tenant,
                "tk": tid,
                "e": estado_inicial,
                "p": _PRIORIDAD_TICKET_A_JOB.get(ticket["prioridad"], "P2"),
                "ctx": json.dumps(
                    {
                        "titulo": ticket["titulo"],
                        "descripcion": ticket["descripcion"],
                        "origen": origen,
                    }
                ),
            },
        )

        # ---- Marcar el inbox como procesado ----
        await db.execute(
            text("""
                UPDATE core.webhook_inbox
                SET estado='procesado', procesado_en=now(), ticket_id=:tk
                WHERE id=:i
            """),
            {"tk": tid, "i": inbox_id},
        )

        ref = creado["numero"] or creado["id"]

        # ---- Evento de auditoría (misma transacción) ----
        await registrar_evento(
            db,
            tenant,
            evento="ticket_creado",
            resumen=f"Ticket creado desde {origen}",
            actor_tipo="sistema",
            ticket_id=ref,
        )

    # KC-BE-04-2: no se encola a kc:triage. El orquestador consulta core.jobs
    # directamente cada 2 segundos y encontrará el job recién creado.
    log.info(
        "normalizer.ticket",
        inbox_id=inbox_id,
        ticket_id=ref,
        job_id=trabajo_id,  # NUEVO: trazabilidad del job creado
        origen=origen,
        encolado_por_cuota=pausado,
    )


# NOTA: `procesar_triage` se ha eliminado en KC-BE-04-2. El triage completo
# (classify + dedup + impact) lo ejecuta el orquestador sobre core.jobs
# (ver apps/workers/src/kallicode_workers/orquestador/etapas.py).
