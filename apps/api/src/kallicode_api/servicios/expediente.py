"""Expediente de auditoría de un ticket (§11.2): lo usan GET /audit/tickets/{numero} y el PDF
que genera el worker de exportaciones (KC-PT-08-3), para que muestren lo mismo."""

from __future__ import annotations

from kallicode_core.auditoria import expediente_ticket, verificar_cadena
from kallicode_core.db import todos, uno
from sqlalchemy.ext.asyncio import AsyncSession


async def datos_expediente(db: AsyncSession, tenant_id: str, numero: str) -> dict | None:
    """Solicitante, integridad, firmas, decisiones por etapa (pasos LLM con modelo, tier,
    confianza y tokens) y eventos completos. None si el ticket no existe en el tenant."""
    t = await uno(
        db,
        "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
        {"t": tenant_id, "n": numero},
    )
    if not t:
        return None
    reporter = t["reportado_por"] and await uno(
        db,
        "SELECT nombre, rol FROM core.users WHERE id=:i",
        {"i": t["reportado_por"]},
    )
    firmas = await todos(
        db,
        """
        SELECT g.gate, g.accion, u.nombre AS actor, g.creado_en AS fecha,
               g.comentario, g.sello
          FROM core.gate_signatures g JOIN core.users u ON u.id = g.actor_id
         WHERE g.ticket_id=:tk ORDER BY g.creado_en""",
        {"tk": t["id"]},
    )
    pasos = await todos(
        db,
        """
        SELECT etapa, paso, modelo, tier, confianza, validacion,
               tokens_in + tokens_out AS tokens, duracion_ms, id AS step_id
          FROM core.llm_steps WHERE ticket_id=:tk ORDER BY creado_en""",
        {"tk": t["id"]},
    )
    eventos = await expediente_ticket(db, tenant_id, numero)
    integridad = await verificar_cadena(db, tenant_id)
    etapas: dict[str, list] = {}
    for p in pasos:
        etapas.setdefault(p["etapa"], []).append(p)
    return {
        "solicitante": {
            "nombre": reporter and reporter["nombre"],
            "origen": t["origen"],
            "fecha": t["creado_en"],
        },
        "integridad": {
            "integra": integridad["integra"],
            "hash_raiz": eventos[-1]["sello"] if eventos else None,
        },
        "firmas": firmas,
        "etapas": [{"etapa": e, "pasos": ps} for e, ps in etapas.items()],
        "eventos": eventos,
    }
