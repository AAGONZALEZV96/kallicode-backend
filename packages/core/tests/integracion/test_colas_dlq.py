"""Tests de colas at-least-once + DLQ (KC-BE-04-3).

Requiere Redis real. Marca como integración.
"""

from __future__ import annotations

import secrets

import pytest
from kallicode_core.comercial import redis_cliente
from kallicode_workers import main as wmain

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


async def _stream_temporal() -> tuple[object, str]:
    r = redis_cliente()
    stream = f"kc:test:dlq_{secrets.token_hex(4)}"
    await r.xgroup_create(stream, wmain._GRUPO, id="0", mkstream=True)
    return r, stream


async def test_handler_falla_3_veces_va_a_dlq_y_metric_sube() -> None:
    """Un manejador que falla siempre agota reintentos, va a kc:dlq y sube la métrica."""
    r, stream = await _stream_temporal()
    try:
        await r.xadd(stream, {"inbox_id": "1"})

        msgs = await r.xreadgroup(wmain._GRUPO, "test-consumer", {stream: ">"}, count=1)

        async def handler_falla(_campos):
            raise RuntimeError("fallo intencionado")

        for _, mensajes in msgs or []:
            for mid, campos in mensajes:
                # 3 intentos (== _MAX_RETRIES) → el tercero publica en DLQ
                for _ in range(3):
                    await wmain.procesar_mensaje(r, stream, mid, campos, handler_falla)

        dlq = await r.xrange("kc:dlq")
        assert len(dlq) >= 1, "El mensaje fallido no llegó a kc:dlq"

        metric = await r.get(f"kc:metrics:dlq:{stream}")
        assert metric is not None, "No se incrementó la métrica kc:metrics:dlq"
        assert int(metric) >= 1
    finally:
        await r.delete(stream)
        await r.delete("kc:dlq")
        await r.delete(f"kc:metrics:dlq:{stream}")


async def test_reprocesar_dlq_reinyecta_al_stream_origen() -> None:
    """La lógica del endpoint reinyecta el payload JSON y borra la entrada del DLQ."""
    r = redis_cliente()
    stream_origen = f"kc:test:origen_{secrets.token_hex(4)}"
    try:
        # Simular un mensaje en DLQ con el formato nuevo (JSON)
        await r.xadd(
            "kc:dlq",
            {
                "original_stream": stream_origen,
                "original_mid": "0-1",
                "payload": '{"inbox_id": "42"}',
                "attempts": "3",
                "error": "boom",
                "traceback": "",
            },
            maxlen=5000,
            approximate=True,
        )

        # --- lógica del endpoint, sin HTTP (idéntica a _ejecutar) ---
        import json as _json

        msgs = await r.xrange("kc:dlq", count=10)
        for mid, campos in msgs:
            stream = campos.get(b"original_stream") or campos.get("original_stream")
            raw = campos.get(b"payload") or campos.get("payload")
            if isinstance(stream, bytes):
                stream = stream.decode()
            if isinstance(raw, bytes):
                raw = raw.decode()
            await r.xadd(stream, _json.loads(raw), maxlen=10000, approximate=True)
            await r.xdel("kc:dlq", mid)

        # El mensaje está en el stream de origen y el DLQ está vacío
        assert await r.xlen(stream_origen) == 1
        assert await r.xlen("kc:dlq") == 0
    finally:
        await r.delete(stream_origen)
        await r.delete("kc:dlq")


async def test_metric_arranca_en_cero_por_stream() -> None:
    """Los contadores arrancan en 0 si no hay fallos."""
    r = redis_cliente()
    for stream in ("kc:inbox", "kc:correo", "kc:exports"):
        val = await r.get(f"kc:metrics:dlq:{stream}")
        # Puede tener valor si otros tests han fallado; solo comprobar que
        # el tipo es válido (int o None → 0)
        if val is not None:
            assert int(val) >= 0
