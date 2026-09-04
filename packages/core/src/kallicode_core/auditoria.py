"""Auditoría inmutable con hash encadenado (§11 del diseño, tabla audit.audit_events).

El sellado lo hace la BASE DE DATOS (trigger T3): este módulo solo inserta el
evento dentro de la MISMA transacción que la mutación de negocio — estado y
registro son atómicos. Alterar un evento rompe todos los siguientes
(audit.verify_chain lo detecta).

Uso:
    sello = await registrar_evento(db, tenant, evento="gate_firmado",
                                   resumen="Gate 1 aprobado", actor_tipo="humano",
                                   actor_id=user_id, ticket_id=..., datos={...})
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db import todos, uno
from .logging import log

# Códigos de evento estables (los consume el explorador de auditoría):
EVENTOS = (
    "ticket_creado", "ticket_modificado", "ticket_cancelado",
    "spec_generada", "gate_firmado", "gate_devuelto",
    "job_creado", "job_transicion", "paso_llm",
    "qa_corrida", "hallazgo_seguridad", "deploy_ejecutado",
    "consumo_registrado", "ciclo_renovado",
    "usuario_creado", "usuario_invitado", "usuario_modificado",
    "sesion_iniciada", "sesion_cerrada", "sesiones_revocadas",
    "password_restablecida", "organizacion_modificada",
    "conexion_creada", "conexion_modificada", "conexion_eliminada",
    "llave_modelo_registrada", "modelos_configurados",
    "fabrica_activada", "auditoria_exportada", "escalado_a_humano",
    "configuracion_modificada",
)


async def registrar_evento(db: AsyncSession, tenant_id: str, *, evento: str,
                           resumen: str, actor_tipo: str, actor_id: str | None = None,
                           ticket_id: str | None = None, job_id: str | None = None,
                           etapa: str | None = None, modelo: str | None = None,
                           step_id: str | None = None,
                           datos: dict[str, Any] | None = None) -> str:
    """Inserta un evento en la cadena y devuelve su sello (hash SHA-256).

    Parámetros:
        evento: código estable del catálogo EVENTOS.
        resumen: texto visible en el portal (idioma del tenant).
        actor_tipo: humano | agente | sistema.
        actor_id: user_id, nombre del agente, o None para sistema.
        datos: payload estructurado del evento (JSONB).
    Salida: sello hex de 64 caracteres, calculado por el trigger T3.
    """
    import json
    fila = await uno(db, """
        INSERT INTO audit.audit_events
            (tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id,
             evento, resumen, datos, modelo, step_id, sello_previo, sello)
        VALUES (:t, :tk, :j, :e, :at, :ai, :ev, :rs, CAST(:dt AS jsonb), :mo, :st, '', '')
        RETURNING sello
    """, {"t": tenant_id, "tk": ticket_id, "j": job_id, "e": etapa,
          "at": actor_tipo, "ai": actor_id, "ev": evento, "rs": resumen,
          "dt": json.dumps(datos) if datos is not None else None,
          "mo": modelo, "st": step_id})
    sello = fila["sello"]
    log.debug("auditoria.evento", codigo=evento, sello=sello[:12])
    return sello


async def expediente_ticket(db: AsyncSession, tenant_id: str, ticket_id: str) -> list[dict]:
    """Todos los eventos de un ticket en orden cronológico."""
    return await todos(db, """
        SELECT id, creado_en, etapa, actor_tipo, actor_id, evento, resumen,
               datos, modelo, step_id, sello
          FROM audit.audit_events
         WHERE tenant_id = :t AND ticket_id = :tk
         ORDER BY id
    """, {"t": tenant_id, "tk": ticket_id})


async def verificar_cadena(db: AsyncSession, tenant_id: str,
                           desde=None, hasta=None) -> dict:
    """Recorre la cadena recomputando sellos (función SQL audit.verify_chain)."""
    fila = await uno(db, """
        SELECT integra, eventos_verificados, primer_evento_roto
          FROM audit.verify_chain(:t, :d, :h)
    """, {"t": tenant_id, "d": desde, "h": hasta})
    if fila and not fila["integra"]:
        log.critical("auditoria.cadena_rota", tenant=tenant_id,
                     primer_evento=fila["primer_evento_roto"])
    return fila or {"integra": True, "eventos_verificados": 0, "primer_evento_roto": None}
