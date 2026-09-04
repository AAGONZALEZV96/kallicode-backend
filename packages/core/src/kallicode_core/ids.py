"""Identificadores ULID con prefijo semántico (§3.1 del diseño).

Ejemplos: u_01J..., tk_01J..., job_01J..., st_01J...
El correlativo visible de tickets (KC-####) NO se genera aquí: lo asigna el
trigger T2 de la base de datos, serializado por tenant.
"""
from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    t = int(time.time() * 1000)
    ts = ""
    for _ in range(10):
        ts = _CROCKFORD[t & 31] + ts
        t >>= 5
    aleatorio = os.urandom(10)
    acc = int.from_bytes(aleatorio, "big")
    rnd = ""
    for _ in range(16):
        rnd = _CROCKFORD[acc & 31] + rnd
        acc >>= 5
    return ts + rnd


def nuevo(prefijo: str) -> str:
    """Genera un id: nuevo('tk') -> 'tk_01J8ZC3AH9...'"""
    return f"{prefijo}_{_ulid()}"
