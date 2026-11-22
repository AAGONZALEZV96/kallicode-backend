"""Expediente de auditoría completo de un ticket (§11.3, KC-BE-08-6).

Lo comparten GET /audit/tickets/{numero} y el PDF del worker para que el
Portal y la exportación presenten el mismo recorrido probatorio.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from kallicode_core.auditoria import expediente_ticket, verificar_cadena
from kallicode_core.db import todos, uno
from kallicode_core.logging import log
from sqlalchemy.ext.asyncio import AsyncSession

from .blob import leer_json_servidor

# Un diff más largo que esto se entrega cortado (diff_truncado) para no inflar el expediente;
# hash_sha256 es siempre el del diff completo, que sigue en la traza (referencia). KC-PT-08-1.
MAX_CARACTERES_DIFF = 200_000

_ARCHIVO_DIFF = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)


def _archivo_del_diff(diff: str, paso: str) -> str:
    coincidencia = _ARCHIVO_DIFF.search(diff)
    if coincidencia and coincidencia.group(1) != "/dev/null":
        return coincidencia.group(1).strip()
    return paso


async def _cambio_desde_traza(paso: dict, pr_url: str | None) -> dict | None:
    ref = paso["salida_ref"]
    if not ref or ref.startswith("mem://"):
        return None
    try:
        salida = await leer_json_servidor(ref)
    except Exception as exc:
        log.warning(
            "audit.expediente.diff_no_disponible",
            step_id=paso["step_id"],
            ref=ref,
            error=str(exc),
        )
        return None

    diff = salida.get("diff")
    if salida.get("needs_replan") or not isinstance(diff, str) or not diff:
        return None
    racional = salida.get("rationale_en") or salida.get("racional") or ""
    return {
        "archivo": _archivo_del_diff(diff, paso["paso"]),
        "pr_url": pr_url,
        "racional": str(racional),
        "referencia": ref,
        "hash_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "diff": diff[:MAX_CARACTERES_DIFF],
        "diff_truncado": len(diff) > MAX_CARACTERES_DIFF,
    }


async def datos_expediente(db: AsyncSession, tenant_id: str, numero: str) -> dict | None:
    """Recorrido probatorio: origen, pasos, diffs, firmas, exports e integridad."""
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
        WITH build_confirmado AS (
            SELECT j.id AS job_id, max(tr.creado_en) AS fin
              FROM core.jobs j
              JOIN core.job_transitions tr ON tr.job_id = j.id AND tr.tenant_id = j.tenant_id
             WHERE j.ticket_id = :tk AND tr.de_estado = 'build' AND tr.a_estado = 'qa'
             GROUP BY j.id
        ), ventana_build AS (
            SELECT b.job_id, b.fin, max(p.creado_en) AS inicio
              FROM build_confirmado b
              JOIN core.llm_steps p ON p.job_id = b.job_id
             WHERE p.ticket_id = :tk AND p.etapa = 'build' AND p.paso = 'build.plan'
               AND p.creado_en <= b.fin
             GROUP BY b.job_id, b.fin
        )
        SELECT p.etapa, p.paso, p.modelo, p.tier, p.confianza::float8 AS confianza, p.validacion,
               p.tokens_in + p.tokens_out AS tokens, p.duracion_ms, p.id AS step_id,
               p.salida_ref, p.job_id,
               (p.etapa = 'build' AND p.paso LIKE 'build.diff.%'
                 AND p.creado_en BETWEEN b.inicio AND b.fin) AS diff_aplicado
          FROM core.llm_steps p LEFT JOIN ventana_build b ON b.job_id = p.job_id
         WHERE p.ticket_id = :tk ORDER BY p.creado_en, p.id""",
        {"tk": t["id"]},
    )
    exportaciones = await todos(
        db,
        """
        SELECT e.id AS export_id, e.formato, e.estado, e.blob_ref AS referencia,
               u.nombre AS solicitado_por, e.creado_en AS solicitada_en,
               e.completado_en
          FROM core.audit_exports e
          JOIN core.users u ON u.id = e.solicitado_por
         WHERE e.ticket_id=:tk
         ORDER BY e.creado_en, e.id""",
        {"tk": t["id"]},
    )
    eventos = await expediente_ticket(db, tenant_id, numero)
    if eventos:
        desde = eventos[0]["creado_en"]
        hasta = eventos[-1]["creado_en"]
        integridad = await verificar_cadena(db, tenant_id, desde, hasta, ticket_id=numero)
    else:
        desde = None
        hasta = None
        integridad = {
            "integra": True,
            "alcance": "tramo_ticket",
            "tramo_integro": True,
            "eventos_verificados": 0,
            "primer_evento_roto": None,
        }

    etapas: dict[str, list] = {}
    diffs_por_paso: dict[tuple[str, str], dict] = {}
    for p in pasos:
        diff_aplicado = p.pop("diff_aplicado")
        job_id = p.pop("job_id")
        salida_ref = p.pop("salida_ref")
        etapas.setdefault(p["etapa"], []).append(p)
        if diff_aplicado:
            diffs_por_paso[job_id, p["paso"]] = {**p, "salida_ref": salida_ref}
    cambios_crudos = await asyncio.gather(
        *(_cambio_desde_traza(p, t["pr_url"]) for p in diffs_por_paso.values())
    )
    cambios = [c for c in cambios_crudos if c is not None]
    return {
        "solicitante": {
            "nombre": reporter and reporter["nombre"],
            "origen": t["origen"],
            "fecha": t["creado_en"],
        },
        "integridad": {
            "integra": integridad["integra"],
            "alcance": "tramo_ticket",
            "tramo_integro": integridad["tramo_integro"],
            "eventos_verificados": integridad["eventos_verificados"],
            "primer_evento_roto": integridad["primer_evento_roto"],
            "desde": desde,
            "hasta": hasta,
            "hash_raiz": eventos[-1]["sello"] if eventos else None,
        },
        "firmas": firmas,
        "etapas": [{"etapa": e, "pasos": ps} for e, ps in etapas.items()],
        "cambios": cambios,
        "exportaciones": exportaciones,
        "eventos": [
            {
                "id": e["id"],
                "creado_en": e["creado_en"],
                "actor_tipo": e["actor_tipo"],
                "actor_id": e["actor_id"],
                # El PDF probatorio conserva el código estable; ExpedienteOut lo
                # retira de la respuesta pública porque Portal no lo consume.
                "evento": e["evento"],
                "resumen": e["resumen"],
                "sello": e["sello"],
            }
            for e in eventos
        ],
    }
