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

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db import todos, uno
from .errors import AppError
from .logging import log

# Presupuesto de minimización y tamaño duro de la columna JSONB `datos` (KC-BE-08-3 / BE-F31):
MAX_TEXTO_AUDITORIA = 512
UMBRAL_BLOB_AUDITORIA_BYTES = 2_048
MAX_BYTES_DATOS_AUDITORIA = 8_192

# Códigos de evento estables (los consume el explorador de auditoría):
EVENTOS = (
    "ticket_creado",
    "ticket_modificado",
    "ticket_cancelado",
    "spec_generada",
    "gate_firmado",
    "gate_devuelto",
    "job_creado",
    "job_transicion",
    "paso_llm",
    "qa_corrida",
    "hallazgo_seguridad",
    "deploy_ejecutado",
    "consumo_registrado",
    "almacenamiento_aviso_80",
    "ciclo_renovado",
    "mejora_plan_solicitada",
    "mejora_plan_aplicada",
    "usuario_creado",
    "usuario_invitado",
    "usuario_modificado",
    "sesion_iniciada",
    "sesion_cerrada",
    "sesiones_revocadas",
    "password_restablecida",
    "organizacion_modificada",
    "conexion_creada",
    "conexion_modificada",
    "conexion_eliminada",
    "llave_modelo_registrada",
    "modelos_configurados",
    "fabrica_activada",
    "auditoria_exportada",
    "escalado_a_humano",
    "configuracion_modificada",
    "curacion_resuelta",
    "qa_escalado",
    "qa_flaky",
    "deploy_preflight",
    "webhook_secreto_rotado",
)

# Gobernanza (KC-BE-08-3 / BE-F31):
# Cada evento del catálogo admite ÚNICAMENTE las claves declaradas en esta allowlist.
# Cualquier ampliación de claves o límites requiere aprobación expresa del Tech Lead
# en revisión de código antes de fusionarse a `develop`.
ALLOWLIST_DATOS_EVENTO: dict[str, tuple[str, ...]] = {
    "ticket_creado": ("tipo", "prioridad", "origen"),
    "ticket_modificado": ("campos", "prioridad", "estado"),
    "ticket_cancelado": ("motivo_cancelacion",),
    "spec_generada": ("version", "alternativas"),
    "gate_firmado": ("gate", "alternativa", "acepta_fallos"),
    "gate_devuelto": ("gate", "iteracion", "destino"),
    "job_creado": ("linea", "etapa"),
    "job_transicion": (
        "de",
        "a",
        "motivo",
        "tipo",
        "prioridad",
        "complejidad",
        "duplicado_de",
        "impacto_radio",
        "blob_ref",
        "blob_sha256",
    ),
    "paso_llm": (
        "paso",
        "tier",
        "confianza",
        "validacion",
        "reintentos",
        "tokens",
        "tokens_in",
        "tokens_out",
        "duracion_ms",
    ),
    # total_evidencias y evidencias_sha256: huella de la evidencia de la corrida (KC-BE-08-5).
    "qa_corrida": (
        "corrida",
        "accion",
        "total_criterios",
        "pasados",
        "total_evidencias",
        "evidencias_sha256",
    ),
    "qa_escalado": ("criterios", "corrida", "total_evidencias", "evidencias_sha256"),
    "qa_flaky": ("criterios", "corrida"),
    "hallazgo_seguridad": (
        "severidad",
        "justificacion",
        "veredicto",
        "bloquea",
        "fix_diff_ref",
    ),
    "deploy_preflight": ("faltantes", "condiciones", "destino", "veredicto"),
    "deploy_ejecutado": ("post_deploy", "anomalia", "destino", "resultado"),
    "consumo_registrado": ("pr_url", "netas"),
    "almacenamiento_aviso_80": ("usados_bytes", "limite_bytes", "porcentaje"),
    "ciclo_renovado": ("ciclo",),
    "mejora_plan_solicitada": ("solicitud_id", "plan_deseado", "urgente"),
    "mejora_plan_aplicada": ("solicitud_id", "plan_deseado"),
    "usuario_creado": ("invitado_por", "rol"),
    "usuario_invitado": ("rol",),
    "usuario_modificado": ("user_id", "nombre", "rol", "estado", "idioma"),
    "sesion_iniciada": (),
    "sesion_cerrada": (),
    "sesiones_revocadas": (),
    "password_restablecida": (),
    "organizacion_modificada": ("campos",),
    "conexion_creada": ("categoria", "proveedor"),
    "conexion_modificada": ("categoria", "proveedor"),
    "conexion_eliminada": ("categoria", "proveedor"),
    "webhook_secreto_rotado": ("connection_id", "anterior_expira_en"),
    "llave_modelo_registrada": ("proveedor",),
    "modelos_configurados": ("asignaciones",),
    "fabrica_activada": ("lineas",),
    "auditoria_exportada": ("export_id", "formato"),
    "escalado_a_humano": (
        "motivo",
        "severidad",
        "paso",
        "criterios",
        "corrida",
        "resumen_detalle",
        "blob_ref",
        "blob_sha256",
    ),
    "configuracion_modificada": ("urgente", "feature", "staging", "produccion"),
    "curacion_resuelta": ("decision", "nugget_id", "comentario"),
}


def _truncar_valor(valor: Any, max_texto: int) -> Any:
    if isinstance(valor, str):
        if len(valor) <= max_texto:
            return valor
        return valor[:max_texto] + "…[truncado]"
    if isinstance(valor, list):
        return [_truncar_valor(v, max_texto) for v in valor]
    if isinstance(valor, dict):
        return {str(k): _truncar_valor(v, max_texto) for k, v in valor.items()}
    return valor


def calcular_sha256_payload(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def depurar_datos_evento(
    evento: str,
    datos: dict[str, Any] | None,
    *,
    max_texto: int = MAX_TEXTO_AUDITORIA,
    max_bytes: int = MAX_BYTES_DATOS_AUDITORIA,
    blob_ref: str | None = None,
    blob_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Recorta `datos` a la allowlist del evento, trunca textos y adjunta referencia a Blob."""
    if datos is None and not blob_ref:
        return None

    permitidas = set(ALLOWLIST_DATOS_EVENTO.get(evento, ()))
    depurado: dict[str, Any] = {}
    if datos:
        for clave, valor in datos.items():
            if clave in permitidas and valor is not None:
                depurado[clave] = _truncar_valor(valor, max_texto)

    if blob_ref and "blob_ref" in permitidas:
        depurado["blob_ref"] = _truncar_valor(blob_ref, max_texto)
        if blob_sha256 and "blob_sha256" in permitidas:
            depurado["blob_sha256"] = blob_sha256

    if not depurado:
        return None

    serializado = json.dumps(depurado, ensure_ascii=False).encode("utf-8")
    if len(serializado) > max_bytes:
        raise AppError(
            "PAYLOAD_AUDITORIA_EXCEDIDO",
            422,
            f"El payload depurado de auditoría para '{evento}' ({len(serializado)} B) "
            f"excede el límite máximo permitido de {max_bytes} B.",
        )
    return depurado


async def registrar_evento(
    db: AsyncSession,
    tenant_id: str,
    *,
    evento: str,
    resumen: str,
    actor_tipo: str,
    actor_id: str | None = None,
    ticket_id: str | None = None,
    job_id: str | None = None,
    etapa: str | None = None,
    modelo: str | None = None,
    step_id: str | None = None,
    datos: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> str:
    """Inserta un evento en la cadena y devuelve su sello (hash SHA-256).

    Parámetros:
        evento: código estable del catálogo EVENTOS.
        resumen: texto visible en el portal.
        actor_tipo: humano | agente | sistema.
        actor_id: user_id, nombre del agente o None para sistema.
        datos: payload estructurado del evento (JSONB).
        ip: dirección IPv4/IPv6 de origen (se castea a inet en la BD; una IP
            inválida provoca un error de PostgreSQL que revierte el INSERT).
        user_agent: cabecera User-Agent del cliente HTTP.
        request_id: identificador de correlación de la solicitud.

    ip, user_agent y request_id forman parte del sello desde la versión 2
    del formato (KC-DB-05-6): quedan protegidos por la cadena igual que el
    resto del evento.
    """
    datos_depurados = depurar_datos_evento(evento, datos)
    dt_json = (
        json.dumps(datos_depurados, ensure_ascii=False) if datos_depurados is not None else None
    )
    if dt_json is not None and len(dt_json.encode("utf-8")) > MAX_BYTES_DATOS_AUDITORIA:
        raise AppError(
            "PAYLOAD_AUDITORIA_EXCEDIDO",
            422,
            f"El payload de auditoría excede el límite máximo de {MAX_BYTES_DATOS_AUDITORIA} B.",
        )

    fila = await uno(
        db,
        """
        INSERT INTO audit.audit_events (
            tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id,
            evento, resumen, datos, modelo, step_id, ip, user_agent, request_id,
            sello_previo, sello
        )
        VALUES (
            :t, :tk, :j, :e, :at, :ai,
            :ev, :rs, CAST(:dt AS jsonb), :mo, :st, CAST(:ip AS inet), :ua, :rid,
            '', ''
        )
        RETURNING sello
        """,
        {
            "t": tenant_id,
            "tk": ticket_id,
            "j": job_id,
            "e": etapa,
            "at": actor_tipo,
            "ai": actor_id,
            "ev": evento,
            "rs": resumen,
            "dt": dt_json,
            "mo": modelo,
            "st": step_id,
            "ip": ip,
            "ua": user_agent,
            "rid": request_id,
        },
    )
    sello = fila["sello"]
    log.debug(
        "auditoria.evento",
        codigo=evento,
        sello=sello[:12],
        ip=ip,
        request_id=request_id,
    )
    return sello


async def expediente_ticket(db: AsyncSession, tenant_id: str, ticket_id: str) -> list[dict]:
    """Todos los eventos de un ticket en orden cronológico."""
    return await todos(
        db,
        """
        SELECT id, creado_en, etapa, actor_tipo, actor_id, evento, resumen,
               datos, modelo, step_id, sello
          FROM audit.audit_events
         WHERE tenant_id = :t AND ticket_id = :tk
         ORDER BY id
    """,
        {"t": tenant_id, "tk": ticket_id},
    )


async def verificar_cadena(
    db: AsyncSession,
    tenant_id: str,
    desde=None,
    hasta=None,
    *,
    ticket_id: str | None = None,
) -> dict:
    """Recorre la cadena recomputando sellos (audit.verify_chain o tramo acotado por ticket)."""
    if ticket_id:
        fila = await uno(
            db,
            """
            WITH ev AS (
                SELECT e.id,
                       audit.calcular_sello(e) <> e.sello AS sello_malo,
                       (
                           e.sello_previo <> repeat('0', 64)
                           AND NOT EXISTS (
                               SELECT 1
                                 FROM audit.audit_events p
                                WHERE p.tenant_id = e.tenant_id
                                  AND p.sello = e.sello_previo
                           )
                       ) AS eslabon_perdido,
                       EXISTS (
                           SELECT 1
                             FROM audit.audit_events b
                            WHERE b.tenant_id = e.tenant_id
                              AND b.sello_previo = e.sello_previo
                              AND b.id <> e.id
                       ) AS bifurcado
                  FROM audit.audit_events e
                 WHERE e.tenant_id = :t
                   AND e.ticket_id = :tk
                   AND (CAST(:d AS timestamptz) IS NULL OR e.creado_en >= CAST(:d AS timestamptz))
                   AND (CAST(:h AS timestamptz) IS NULL OR e.creado_en <= CAST(:h AS timestamptz))
            )
            SELECT COALESCE(bool_and(NOT (sello_malo OR eslabon_perdido OR bifurcado)), true) AS integra,
                   count(*)::bigint AS eventos_verificados,
                   min(id) FILTER (WHERE sello_malo OR eslabon_perdido OR bifurcado) AS primer_evento_roto
              FROM ev
            """,
            {"t": tenant_id, "tk": ticket_id, "d": desde, "h": hasta},
        )
        integra = bool(fila["integra"]) if fila else True
        if not integra:
            log.critical(
                "auditoria.cadena_rota",
                tenant=tenant_id,
                ticket_id=ticket_id,
                primer_evento=fila["primer_evento_roto"],
            )
        return {
            "integra": integra,
            "alcance": "tramo_ticket",
            "tramo_integro": integra,
            "cadena_tenant_integra": None,
            "eventos_verificados": int(fila["eventos_verificados"]) if fila else 0,
            "primer_evento_roto": fila["primer_evento_roto"] if fila else None,
            "desde": desde,
            "hasta": hasta,
        }

    fila = await uno(
        db,
        """
        SELECT integra, eventos_verificados, primer_evento_roto
          FROM audit.verify_chain(:t, :d, :h)
        """,
        {"t": tenant_id, "d": desde, "h": hasta},
    )
    integra = bool(fila["integra"]) if fila else True
    if not integra:
        log.critical(
            "auditoria.cadena_rota",
            tenant=tenant_id,
            primer_evento=fila["primer_evento_roto"],
        )
    return {
        "integra": integra,
        "alcance": "cadena_tenant",
        "tramo_integro": integra,
        "cadena_tenant_integra": integra,
        "eventos_verificados": int(fila["eventos_verificados"]) if fila else 0,
        "primer_evento_roto": fila["primer_evento_roto"] if fila else None,
        "desde": desde,
        "hasta": hasta,
    }
