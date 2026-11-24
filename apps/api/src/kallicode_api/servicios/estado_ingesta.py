"""Lectura del contrato interno de ingesta en la sesión del tenant."""

from __future__ import annotations

import json
from typing import Any

from kallicode_core.db import todos, uno
from kallicode_core.grafo import fase_confirmada_ingesta
from sqlalchemy.ext.asyncio import AsyncSession

from ..esquemas.grafo import (
    CoberturaGrafoOut,
    CuracionPendienteOut,
    FaseIngestaOut,
    IngestaGrafoStatusOut,
    ProgresoIngestaOut,
)

FASES_ONBOARDING: dict[int, str] = {
    1: "clonado_y_escaneo",
    2: "extraccion_ast_y_simbolos",
    3: "resolucion_esquema_sql_orm",
    4: "enriquecimiento_ontologico_y_embeddings",
    5: "consolidacion_grafo",
}


def normalizar_fase_onboarding(fase_actual: Any, stats: dict[str, Any], estado: str) -> int:
    if estado in ("completo", "vigente"):
        return 5
    candidato = fase_actual if fase_actual is not None else stats.get("checkpoint_fase", 0)
    if isinstance(candidato, int):
        return max(0, min(5, candidato))
    if isinstance(candidato, str):
        digitos = "".join(ch for ch in candidato if ch.isdigit())
        if digitos:
            return max(0, min(5, int(digitos)))
    return 1 if estado == "en_progreso" else 0


async def leer_estado_ingesta(
    db: AsyncSession, tenant_id: str, commit_sha: str | None = None
) -> IngestaGrafoStatusOut:
    condiciones = ["tenant_id = :t"]
    params: dict[str, Any] = {"t": tenant_id}
    if commit_sha:
        condiciones.append("commit_sha = :sha")
        params["sha"] = commit_sha.lower()

    snap = await uno(
        db,
        f"""
        SELECT id, commit_sha, rama, estado, fase_actual, stats, iniciado_en, finalizado_en
        FROM cm.repo_snapshots
        WHERE {" AND ".join(condiciones)}
        ORDER BY iniciado_en DESC
        LIMIT 1
        """,
        params,
    )

    nodos_por_kind_rows = await todos(
        db,
        """
        SELECT n.kind, count(*)::int AS total, count(e.nodo_ref)::int AS con_embedding
        FROM cm.nodes n
        LEFT JOIN vec.definition_embeddings e
          ON e.tenant_id = n.tenant_id AND e.nodo_ref = n.id
        WHERE n.tenant_id = :t
        GROUP BY n.kind
        """,
        {"t": tenant_id},
    )
    por_kind = {str(r["kind"]): int(r["total"]) for r in nodos_por_kind_rows}
    nodos_totales = sum(por_kind.values())

    aristas_row = await uno(
        db,
        "SELECT count(*)::int AS total FROM cm.edges WHERE tenant_id = :t",
        {"t": tenant_id},
    )
    total_aristas = int(aristas_row["total"]) if aristas_row else 0

    nodos_con_embedding = sum(int(r["con_embedding"]) for r in nodos_por_kind_rows)
    porcentaje_embeddings = 100.0 * nodos_con_embedding / nodos_totales if nodos_totales else 0.0

    curacion_rows = await todos(
        db,
        """
        SELECT tipo, count(*)::int AS total
        FROM cm.curation_queue
        WHERE tenant_id = :t AND estado = 'pendiente'
        GROUP BY tipo
        """,
        {"t": tenant_id},
    )
    curacion_por_tipo = {str(r["tipo"]): int(r["total"]) for r in curacion_rows}
    curacion_total = sum(curacion_por_tipo.values())

    vigente_row = await uno(
        db,
        """
        SELECT actualizado_en
        FROM cm.snapshot_vigente
        WHERE tenant_id = :t
        """,
        {"t": tenant_id},
    )
    ultimo_refresco: str | None = None
    if vigente_row and vigente_row.get("actualizado_en") is not None:
        act = vigente_row["actualizado_en"]
        ultimo_refresco = act.isoformat() if hasattr(act, "isoformat") else str(act)
    else:
        ultimo_comp = await uno(
            db,
            """
            SELECT finalizado_en
            FROM cm.repo_snapshots
            WHERE tenant_id = :t AND estado = 'completo' AND finalizado_en IS NOT NULL
            ORDER BY finalizado_en DESC
            LIMIT 1
            """,
            {"t": tenant_id},
        )
        if ultimo_comp and ultimo_comp.get("finalizado_en") is not None:
            fin = ultimo_comp["finalizado_en"]
            ultimo_refresco = fin.isoformat() if hasattr(fin, "isoformat") else str(fin)

    cobertura = CoberturaGrafoOut(
        nodos_totales=nodos_totales,
        nodos_con_embedding=nodos_con_embedding,
        porcentaje_embeddings=porcentaje_embeddings,
        por_kind=por_kind,
    )
    curacion = CuracionPendienteOut(
        total=curacion_total,
        por_tipo=curacion_por_tipo,
    )

    if not snap:
        return IngestaGrafoStatusOut(
            tenant_id=tenant_id,
            estado="sin_iniciar",
            fase=FaseIngestaOut(numero=0, nombre="sin_iniciar", checkpoint=None),
            progreso=ProgresoIngestaOut(
                porcentaje=0,
                nodos=nodos_totales,
                aristas=total_aristas,
                embeddings=nodos_con_embedding,
            ),
            cobertura=cobertura,
            curacion_pendiente=curacion,
            ultimo_refresco=ultimo_refresco,
        )

    raw_stats = snap.get("stats") or {}
    stats = raw_stats if isinstance(raw_stats, dict) else json.loads(raw_stats)
    estado = str(snap["estado"])
    checkpoint = fase_confirmada_ingesta(stats)
    fase_num = normalizar_fase_onboarding(
        checkpoint if checkpoint is not None else snap.get("fase_actual"), stats, estado
    )

    if estado in ("completo", "vigente"):
        porcentaje = 100
    elif fase_num <= 0:
        porcentaje = 0
    else:
        porcentaje = min(95, fase_num * 20)

    escalacion_activa = (
        stats.get("escalacion")
        if estado == "pendiente_intervencion" and isinstance(stats.get("escalacion"), dict)
        else None
    )

    iniciado_en = snap.get("iniciado_en")
    finalizado_en = snap.get("finalizado_en")

    return IngestaGrafoStatusOut(
        tenant_id=tenant_id,
        snapshot_id=str(snap["id"]),
        commit_sha=str(snap["commit_sha"]),
        rama=str(snap["rama"]),
        estado=estado,
        fase=FaseIngestaOut(
            numero=fase_num,
            nombre=FASES_ONBOARDING.get(fase_num, "inicializando"),
            checkpoint=str(snap["fase_actual"]) if snap.get("fase_actual") is not None else None,
            checkpoint_numero=checkpoint,
        ),
        progreso=ProgresoIngestaOut(
            porcentaje=porcentaje,
            nodos=nodos_totales,
            aristas=total_aristas,
            embeddings=nodos_con_embedding,
        ),
        cobertura=cobertura,
        curacion_pendiente=curacion,
        ultimo_refresco=ultimo_refresco,
        escalacion_activa=escalacion_activa,
        iniciado_en=iniciado_en.isoformat() if hasattr(iniciado_en, "isoformat") else iniciado_en,
        finalizado_en=(
            finalizado_en.isoformat() if hasattr(finalizado_en, "isoformat") else finalizado_en
        ),
    )
