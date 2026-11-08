"""API de curación de CodeMapping (KC-CM-07-3) y selectores de la UI (KC-CM-07-5).

Los enlaces y conceptos con confianza inferior al umbral no entran al grafo: quedan en
cm.curation_queue hasta que una persona los aprueba o rechaza. RLS confina cada consulta al tenant
del token (`sesion_de`), así que un id de otro tenant se ve como inexistente.

Ruta de lectura: cualquier rol autenticado. Decisión: owner, admin, architect y approver.
Todo texto que sale de aquí proviene de un LLM leyendo código ajeno; la UI debe pintarlo como texto.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.logging import log
from pydantic import BaseModel, Field

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual
from ..servicios.curacion import ESTADOS, TIPOS, normalizar_item, sanear_texto

router = APIRouter(prefix="/codemapping/curation", tags=["Curación CodeMapping"])

# Confianza numérica del payload; NULL si el payload no la trae o no es un número.
_CONFIANZA = (
    "CASE WHEN jsonb_typeof(payload->'confidence') = 'number' "
    "THEN (payload->>'confidence')::numeric END"
)
_COLUMNAS = "id, tipo, payload, estado, revisado_por, revisado_en, creado_en"


class CuracionDecisionIn(BaseModel):
    decision: Literal["aprobado", "rechazado"]
    comentario: str | None = Field(default=None, max_length=500)


@router.get("/selectores", summary="Opciones para los selectores de la UI de curación")
async def selectores(usuario: UsuarioActual = Depends(usuario_actual)) -> dict[str, Any]:
    """Valores que la UI necesita para pintar sus filtros, con conteos del tenant.

    Salida: estados y tipos (siempre completos, con conteo 0 si no hay), snapshots con su número
    de pendientes (los 50 más recientes), rango de confianza observado y umbral vigente.
    """
    async with sesion_de(usuario) as db:
        por_estado = await todos(
            db, "SELECT estado, count(*) AS n FROM cm.curation_queue GROUP BY estado"
        )
        por_tipo = await todos(
            db, "SELECT tipo, count(*) AS n FROM cm.curation_queue GROUP BY tipo"
        )
        snapshots = await todos(
            db,
            """
            SELECT s.id, s.estado, s.rama, s.commit_sha, s.iniciado_en,
                   count(q.id) FILTER (WHERE q.estado = 'pendiente') AS pendientes
              FROM cm.repo_snapshots s
              LEFT JOIN cm.curation_queue q
                     ON q.tenant_id = s.tenant_id AND q.payload->>'snapshot_id' = s.id
             GROUP BY s.tenant_id, s.id
             ORDER BY s.iniciado_en DESC
             LIMIT 50""",
        )
        rango = await uno(
            db,
            f"SELECT min({_CONFIANZA}) AS minima, max({_CONFIANZA}) AS maxima "
            "FROM cm.curation_queue",
        )
        umbral = await uno(db, "SELECT cm.umbral_confianza() AS umbral")

    n_estado = {f["estado"]: int(f["n"]) for f in por_estado}
    n_tipo = {f["tipo"]: int(f["n"]) for f in por_tipo}
    return {
        "estados": [{"valor": e, "total": n_estado.get(e, 0)} for e in ESTADOS],
        "tipos": [{"valor": t, "total": n_tipo.get(t, 0)} for t in TIPOS],
        "snapshots": [
            {
                "id": s["id"],
                "estado": s["estado"],
                "rama": sanear_texto(str(s["rama"]), 200),
                "commit_sha": s["commit_sha"],
                "iniciado_en": s["iniciado_en"],
                "pendientes": int(s["pendientes"]),
            }
            for s in snapshots
        ],
        "confianza": {
            "minima": float(rango["minima"]) if rango and rango["minima"] is not None else None,
            "maxima": float(rango["maxima"]) if rango and rango["maxima"] is not None else None,
        },
        "umbral": float(umbral["umbral"]) if umbral else None,
    }


@router.get("", summary="Listar elementos de la cola de curación")
async def listar(
    estado: Literal["pendiente", "aprobado", "rechazado"] | None = None,
    tipo: Literal["concepto", "enlace", "nugget"] | None = None,
    snapshot_id: str | None = Query(default=None, max_length=100),
    confianza_min: float | None = Query(default=None, ge=0, le=1),
    confianza_max: float | None = Query(default=None, ge=0, le=1),
    limite: int = Query(default=25, ge=1, le=100),
    desde: int = Query(default=0, ge=0),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict[str, Any]:
    """Salida: {items, total, limite, desde}. Orden: los más dudosos (menor confianza) primero."""
    filtros = ["tenant_id = :t"]
    params: dict[str, Any] = {"t": usuario.tenant_id, "lim": limite, "off": desde}
    if estado:
        filtros.append("estado = :estado")
        params["estado"] = estado
    if tipo:
        filtros.append("tipo = :tipo")
        params["tipo"] = tipo
    if snapshot_id:
        filtros.append("payload->>'snapshot_id' = :snap")
        params["snap"] = snapshot_id
    if confianza_min is not None:
        filtros.append(f"{_CONFIANZA} >= :cmin")
        params["cmin"] = confianza_min
    if confianza_max is not None:
        filtros.append(f"{_CONFIANZA} <= :cmax")
        params["cmax"] = confianza_max
    donde = " AND ".join(filtros)

    async with sesion_de(usuario) as db:
        total = await uno(db, f"SELECT count(*) AS n FROM cm.curation_queue WHERE {donde}", params)
        filas = await todos(
            db,
            f"""SELECT {_COLUMNAS} FROM cm.curation_queue WHERE {donde}
                 ORDER BY {_CONFIANZA} ASC NULLS LAST, creado_en DESC, id
                 LIMIT :lim OFFSET :off""",
            params,
        )
    return {
        "items": [normalizar_item(f) for f in filas],
        "total": int(total["n"]) if total else 0,
        "limite": limite,
        "desde": desde,
    }


@router.get("/{item_id}", summary="Detalle de un elemento de curación")
async def detalle(item_id: str, usuario: UsuarioActual = Depends(usuario_actual)) -> dict[str, Any]:
    """Incluye el payload completo (saneado) para que la persona revise la evidencia."""
    async with sesion_de(usuario) as db:
        fila = await uno(
            db,
            f"SELECT {_COLUMNAS} FROM cm.curation_queue WHERE tenant_id = :t AND id = :i",
            {"t": usuario.tenant_id, "i": item_id},
        )
    if fila is None:
        raise no_encontrado("El elemento de curación")
    return normalizar_item(fila, con_payload=True)


@router.post("/{item_id}/decision", summary="Aprobar o rechazar un elemento")
async def decidir(
    item_id: str,
    datos: CuracionDecisionIn,
    usuario: UsuarioActual = Depends(requiere_rol("owner", "admin", "architect", "approver")),
) -> dict[str, Any]:
    """Solo se resuelve un elemento `pendiente`. Errores: RECURSO_NO_ENCONTRADO (404) ·
    TRANSICION_INVALIDA (409) si ya estaba aprobado o rechazado."""
    # Un nugget (KC-CM-08-2/08-3) no se resuelve con un UPDATE de la cola: cm.resolver_nugget()
    # registra la decisión en la revisión y la aplica al nugget en la misma transacción. Con un
    # UPDATE directo la cola quedaría resuelta y el nugget 'propuesto' para siempre (la base no deja
    # aprobarlo después), y {{nuggets}} nunca lo entregaría. La docstring es la descripción pública
    # de contratos/api-v1.yaml: los detalles de implementación van aquí.
    comentario = sanear_texto(datos.comentario, 500) if datos.comentario else None
    datos_evento: dict[str, Any] = {"item_id": item_id, "decision": datos.decision}
    async with sesion_de(usuario) as db:
        fila = await uno(
            db,
            f"""UPDATE cm.curation_queue
                   SET estado = :e, revisado_por = :u, revisado_en = now(),
                       payload = payload || jsonb_build_object(
                           'revision', jsonb_build_object('comentario', CAST(:c AS text)))
                 WHERE tenant_id = :t AND id = :i AND estado = 'pendiente' AND tipo <> 'nugget'
             RETURNING {_COLUMNAS}""",
            {
                "e": datos.decision,
                "u": usuario.user_id,
                "c": comentario,
                "t": usuario.tenant_id,
                "i": item_id,
            },
        )
        if fila is None:
            existente = await uno(
                db,
                """SELECT estado, tipo, payload ->> 'nugget_id' AS nugget_id
                     FROM cm.curation_queue WHERE tenant_id = :t AND id = :i""",
                {"t": usuario.tenant_id, "i": item_id},
            )
            if existente is None:
                raise no_encontrado("El elemento de curación")
            if existente.get("tipo") != "nugget" or existente["estado"] != "pendiente":
                raise AppError(
                    "TRANSICION_INVALIDA",
                    409,
                    f"El elemento ya está {existente['estado']}; solo se resuelven los pendientes.",
                )
            await uno(
                db,
                """SELECT id FROM cm.resolver_nugget(
                       CAST(:n AS text), CAST(:u AS text), CAST(:e AS text), CAST(:c AS text))""",
                {
                    "n": existente["nugget_id"],
                    "u": usuario.user_id,
                    "e": datos.decision,
                    "c": comentario,
                },
            )
            fila = await uno(
                db,
                f"SELECT {_COLUMNAS} FROM cm.curation_queue WHERE tenant_id = :t AND id = :i",
                {"t": usuario.tenant_id, "i": item_id},
            )
            if fila is None:  # pragma: no cover - la fila acaba de resolverse en esta transacción
                raise no_encontrado("El elemento de curación")
            datos_evento["nugget_id"] = existente["nugget_id"]
        await registrar_evento(
            db,
            usuario.tenant_id,
            evento="curacion_resuelta",
            resumen=f"Curación {item_id}: {datos.decision}",
            actor_tipo="humano",
            actor_id=usuario.user_id,
            datos=datos_evento,
        )
    log.info("curacion.resuelta", id=item_id, decision=datos.decision)
    return normalizar_item(fila)
