from typing import Callable, Any, Awaitable
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
import json


async def con_idempotencia(
    db,
    tenant_id: str,
    endpoint_name: str,
    idempotency_key: str,
    codigo_exito: int,
    logica: Callable[[], Awaitable[dict]],
) -> JSONResponse:
    """
    Garantiza idempotencia atómica mediante INSERT ... ON CONFLICT DO NOTHING RETURNING.
    Preserva el código HTTP original en reintentos exitosos.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Falta encabezado Idempotency-Key")

    # 1. Reserva atómica
    query_reserva = text("""
        INSERT INTO core.idempotency_keys (tenant_id, endpoint, clave, respuesta, status_code)
        VALUES (:tenant_id, :endpoint, :clave, '{}'::jsonb, 0)
        ON CONFLICT (tenant_id, endpoint, clave) DO NOTHING
        RETURNING clave;
    """)
    res = await db.execute(query_reserva, {
        "tenant_id": tenant_id,
        "endpoint": endpoint_name,
        "clave": idempotency_key,
    })
    fila = res.first()

    # 2. Conflicto: la clave ya fue registrada previamente
    if not fila:
        query_memo = text("""
            SELECT respuesta, status_code
            FROM core.idempotency_keys
            WHERE tenant_id = :tenant_id AND endpoint = :endpoint AND clave = :clave;
        """)
        memo_res = await db.execute(query_memo, {
            "tenant_id": tenant_id,
            "endpoint": endpoint_name,
            "clave": idempotency_key,
        })
        memo = memo_res.mappings().first()

        if not memo or memo["status_code"] == 0:
            raise HTTPException(status_code=409, detail="Peticion concurrente en proceso con la misma clave")

        return JSONResponse(content=memo["respuesta"], status_code=memo["status_code"])

    # 3. Ejecutar lógica si se obtuvo la reserva
    try:
        data = await logica()

        query_update = text("""
            UPDATE core.idempotency_keys
            SET respuesta = CAST(:resp AS jsonb), status_code = :code
            WHERE tenant_id = :tenant_id AND endpoint = :endpoint AND clave = :clave;
        """)
        await db.execute(query_update, {
            "resp": json.dumps(data),
            "code": codigo_exito,
            "tenant_id": tenant_id,
            "endpoint": endpoint_name,
            "clave": idempotency_key,
        })
        return JSONResponse(content=data, status_code=codigo_exito)

    except Exception:
        # Liberar la clave si la ejecución interna falla para permitir reintentos
        query_delete = text("""
            DELETE FROM core.idempotency_keys
            WHERE tenant_id = :tenant_id AND endpoint = :endpoint AND clave = :clave;
        """)
        await db.execute(query_delete, {
            "tenant_id": tenant_id,
            "endpoint": endpoint_name,
            "clave": idempotency_key,
        })
        raise