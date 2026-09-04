"""Normalizador: webhook_inbox → ticket interno → Triage (§16 del diseño).

procesar_inbox: convierte el payload crudo de cada proveedor al formato único
de ticket. Errores de normalización NUNCA vuelven al proveedor: quedan en la
inbox con estado 'fallido' y ERROR en el log central.

procesar_triage: para tickets nuevos, calcula el embedding (BGE-M3) para la
deduplicación vectorial y ejecuta el paso LLM triage.classify (tier flash,
escalable) — documentado en el catálogo de plantillas.
"""
from __future__ import annotations

import json

import httpx
from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import cuota_agotada, redis_cliente
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, sesion_tenant, uno
from kallicode_core.ids import nuevo
from kallicode_core.logging import log


# ------------------------------------------------------------- normalización
def _de_jira(p: dict) -> dict | None:
    issue = p.get("issue") or {}
    campos = issue.get("fields") or {}
    if p.get("webhookEvent") not in ("jira:issue_created", "jira:issue_updated"):
        return None
    prioridad = {"highest": "alta", "high": "alta", "medium": "media",
                 "low": "baja", "lowest": "baja"}.get(
        str((campos.get("priority") or {}).get("name", "")).lower(), "media")
    return {"tipo": "bug", "titulo": campos.get("summary") or issue.get("key", "Jira"),
            "descripcion": json.dumps(campos.get("description") or "")[:10000] or "-",
            "prioridad": prioridad, "clave_externa": issue.get("key")}


def _de_github(p: dict) -> dict | None:
    if p.get("action") not in ("opened", "edited", "labeled"):
        return None
    issue = p.get("issue") or {}
    etiquetas = {e.get("name") for e in issue.get("labels", [])}
    if "kallicode:skip" in etiquetas:
        return None
    tipo = "mejora" if "enhancement" in etiquetas else "bug"
    return {"tipo": tipo, "titulo": issue.get("title", "GitHub issue"),
            "descripcion": (issue.get("body") or "-")[:10000],
            "prioridad": "media", "clave_externa": str(issue.get("number"))}


def _de_help(p: dict) -> dict | None:
    tipo = {"bug": "bug", "mejora": "mejora", "requerimiento": "funcionalidad"}.get(
        p.get("tipo", "bug"), "bug")
    return {"tipo": tipo, "titulo": p.get("titulo", "Kallicode Help"),
            "descripcion": (p.get("descripcion") or "-")[:10000],
            "prioridad": "alta" if p.get("diagnostico", {}).get("confianza", 0) > 0.8
            else "media",
            "usuario_final": p.get("usuario_final"),
            "diagnostico": p.get("diagnostico")}


def _de_corestream(p: dict) -> dict | None:
    tk = p.get("ticket") or {}
    tipo = {"bug": "bug", "improvement": "mejora"}.get(tk.get("kind", "bug"), "bug")
    return {"tipo": tipo, "titulo": tk.get("title", "Corestream"),
            "descripcion": (tk.get("body") or "-")[:10000],
            "prioridad": {"high": "alta", "low": "baja"}.get(tk.get("priority"), "media"),
            "clave_externa": tk.get("id")}


_NORMALIZADORES = {"jira:issue": _de_jira, "issues": _de_github,
                   "help:ticket": _de_help, "cs:ticket": _de_corestream,
                   "ci:resultado": None}


async def procesar_inbox(campos: dict) -> None:
    """Consume kc:inbox: normaliza una entrada de webhook_inbox a ticket."""
    inbox_id = int(campos["inbox_id"])
    async with sesion_sistema() as db:
        fila = await uno(db, "SELECT * FROM core.webhook_inbox WHERE id=:i "
                             "AND estado='pendiente' FOR UPDATE SKIP LOCKED",
                         {"i": inbox_id})
        if not fila:
            return
        if fila["evento"] == "ci:resultado":
            # correlación con el job por run_id (TODO produccion: señal al orquestador)
            await db.execute(text("UPDATE core.webhook_inbox SET estado='procesado', "
                                  "procesado_en=now() WHERE id=:i"), {"i": inbox_id})
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
            await db.execute(text("UPDATE core.webhook_inbox SET estado=:e, "
                                  "procesado_en=now() WHERE id=:i"),
                             {"e": estado, "i": inbox_id})
            return
        tenant = fila["tenant_id"]
    # crea el ticket dentro del tenant (QU-1: cola por cuota)
    async with sesion_tenant(tenant, actor="svc:normalizer") as db:
        origen = {"jira:issue": "jira", "issues": "github", "help:ticket":
                  "kallicode_help", "cs:ticket": "corestream"}[fila["evento"]]
        pausado = await cuota_agotada(db, tenant)
        tid = nuevo("tk")
        creado = await uno(db, """
            INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion,
                                      prioridad, etapa, origen, origen_ref, usuario_final)
            VALUES (:i, :t, :tipo, :ti, :d, :p, :e, :o, CAST(:oref AS jsonb),
                    CAST(:uf AS jsonb))
            RETURNING numero""",
            {"i": tid, "t": tenant, "tipo": ticket["tipo"], "ti": ticket["titulo"][:200],
             "d": ticket["descripcion"], "p": ticket["prioridad"],
             "e": "en_cola_por_cuota" if pausado else "triage", "o": origen,
             "oref": json.dumps({"clave_externa": ticket.get("clave_externa"),
                                 "diagnostico": ticket.get("diagnostico")}),
             "uf": json.dumps(ticket.get("usuario_final"))
                   if ticket.get("usuario_final") else None})
        await db.execute(text("""UPDATE core.webhook_inbox SET estado='procesado',
                                        procesado_en=now(), ticket_id=:tk WHERE id=:i"""),
                         {"tk": tid, "i": inbox_id})
        await registrar_evento(db, tenant, evento="ticket_creado",
                               resumen=f"Ticket creado desde {origen}",
                               actor_tipo="sistema", ticket_id=creado["numero"])
    if not pausado:
        await redis_cliente().xadd("kc:triage", {"ticket_id": creado["numero"],
                                                 "tenant_id": tenant})
    log.info("normalizer.ticket", inbox_id=inbox_id, ticket_id=creado["numero"],
             origen=origen, encolado_por_cuota=pausado)


# --------------------------------------------------------------------- triage
async def procesar_triage(campos: dict) -> None:
    """Consume kc:triage: embedding de dedup + clasificación LLM (flash).

    Llamada LLM documentada: triage.classify (catálogo de plantillas).
    El resultado NO transiciona el job (eso es del orquestador §17): deja la
    clasificación como contexto y el embedding listo para dedup-preview.
    """
    numero, tenant = campos["ticket_id"], campos["tenant_id"]
    s = get_settings()
    async with sesion_tenant(tenant, actor="svc:triage") as db:
        t = await uno(db, "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                      {"t": tenant, "n": numero})
        if not t or t["etapa"] not in ("triage",):
            return
    # 1) embedding para dedup (best-effort)
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(f"{s.embeddings_url}/v1/embeddings",
                               json={"model": "bge-m3",
                                     "input": f"{t['titulo']}\n{t['descripcion'][:2000]}"})
            r.raise_for_status()
            emb = r.json()["data"][0]["embedding"]
        import hashlib
        async with sesion_tenant(tenant, actor="svc:triage") as db:
            await db.execute(text("""
                INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding,
                                                   texto_hash)
                VALUES (:tk, :t, CAST(:e AS vector), :h)
                ON CONFLICT (ticket_id) DO UPDATE
                    SET embedding = CAST(:e AS vector), texto_hash = :h"""),
                {"tk": t["id"], "t": tenant, "e": str(emb),
                 "h": hashlib.sha256(t["descripcion"].encode()).hexdigest()[:32]})
        log.info("triage.embedding_ok", ticket_id=numero)
    except Exception:
        log.warning("triage.embedding_fallo", ticket_id=numero)
    # 2) clasificación LLM (flash, escalable) — best-effort si no hay LLM local
    try:
        from kallicode_core.llm import ejecutar_paso
        resultado = await ejecutar_paso("triage.classify", {
            "ticket_id": numero, "titulo": t["titulo"], "origen": t["origen"],
            "descripcion": t["descripcion"][:4000], "adjuntos_transcritos": []})
        async with sesion_tenant(tenant, actor="svc:triage") as db:
            await registrar_evento(
                db, tenant, evento="paso_llm", etapa="triage",
                resumen=f"Clasificado como {resultado.salida.get('type')} "
                        f"(confianza {resultado.confianza})",
                actor_tipo="agente", actor_id="triage", ticket_id=numero,
                modelo=resultado.modelo,
                datos={"tier": resultado.tier.value,
                       "tokens": resultado.tokens_in + resultado.tokens_out})
        log.info("triage.clasificado", ticket_id=numero, tier=resultado.tier.value,
                 tipo=resultado.salida.get("type"))
    except Exception as e:
        log.warning("triage.llm_no_disponible", ticket_id=numero,
                    error=type(e).__name__)
