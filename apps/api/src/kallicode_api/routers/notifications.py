"""Notificaciones del portal (§12 del diseño). El frontend hace polling (D12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from kallicode_core.db import todos, uno
from kallicode_core.errors import no_encontrado
from pydantic import BaseModel

from ..deps import UsuarioActual, sesion_de, usuario_actual
from ..paginacion import parse_sort

_SORT_NOTIFICATIONS = {
    "creada_en": "creada_en",
    "tipo": "tipo",
    "leida": "leida",
}

router = APIRouter(prefix="/notifications", tags=["Notificaciones"])


class LeidaIn(BaseModel):
    leida: bool


@router.get("", summary="Listar notificaciones")
async def listar(
    solo_no_leidas: bool = False,
    sort: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    cond = "user_id = :u" + (" AND NOT leida" if solo_no_leidas else "")
    columna, ascending = parse_sort(sort, _SORT_NOTIFICATIONS, default=("creada_en", "desc"))
    direccion = "ASC" if ascending else "DESC"
    async with sesion_de(usuario) as db:
        items = await todos(
            db,
            f"""SELECT id, tipo, titulo, cuerpo, ticket_id,
                      creada_en, leida
                 FROM core.notifications WHERE {cond}
                 ORDER BY {columna} {direccion} LIMIT :lim OFFSET :off""",
            {"u": usuario.user_id, "lim": page_size, "off": (page - 1) * page_size},
        )
        no_leidas = (
            await uno(
                db,
                """SELECT count(*) AS n FROM core.notifications
                     WHERE user_id = :u AND NOT leida""",
                {"u": usuario.user_id},
            )
        )["n"]
    return {
        "items": items,
        "no_leidas": no_leidas,
        "page": page,
        "page_size": page_size,
        "sort": f"{'-' if not ascending else ''}{columna}",
    }


@router.patch("/{notification_id}", summary="Marcar leída / no leída")
async def marcar(
    notification_id: str,
    datos: LeidaIn,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    """Error: NOTIFICACION_NO_ENCONTRADA (404) — solo el destinatario la ve."""
    async with sesion_de(usuario) as db:
        fila = await uno(
            db,
            """UPDATE core.notifications SET leida = :l
                                 WHERE id = :i AND user_id = :u
                                 RETURNING id, leida""",
            {"l": datos.leida, "i": notification_id, "u": usuario.user_id},
        )
    if not fila:
        raise no_encontrado("La notificación")
    return fila


@router.post("/read-all", summary="Marcar todas como leídas")
async def leer_todas(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    async with sesion_de(usuario) as db:
        filas = await todos(
            db,
            """UPDATE core.notifications SET leida = true
                                    WHERE user_id = :u AND NOT leida RETURNING id""",
            {"u": usuario.user_id},
        )
    return {"marcadas": len(filas)}
