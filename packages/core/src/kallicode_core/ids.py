"""Identificadores ULID monótonos con prefijo semántico (§3.1 del diseño).

Ejemplos: u_01J..., tk_01J..., job_01J..., st_01J...
El correlativo visible de tickets (KC-####) NO se genera aquí: lo asigna el
trigger T2 de la base de datos, serializado por tenant.
"""

from __future__ import annotations

import os
import threading
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_BITS_ALEATORIOS = 80
_MAX_ALEATORIO = (1 << _BITS_ALEATORIOS) - 1
# Reservamos los 16 bits superiores en cada nuevo milisegundo para garantizar
# al menos 2**64 incrementos monótonos sin desbordar los 80 bits.
_BYTES_SEMILLA_ALEATORIA = 8

_lock = threading.Lock()
_ultimo_ms: int = -1
_ultimo_rnd: int = 0


def _codificar_crockford(valor: int, longitud: int) -> str:
    chars = [""] * longitud
    for idx in range(longitud - 1, -1, -1):
        chars[idx] = _CROCKFORD[valor & 31]
        valor >>= 5
    return "".join(chars)


def _semilla_aleatoria() -> int:
    return int.from_bytes(os.urandom(_BYTES_SEMILLA_ALEATORIA), "big")


def _ulid() -> str:
    global _ultimo_ms
    global _ultimo_rnd

    ahora_ms = time.time_ns() // 1_000_000
    with _lock:
        if ahora_ms > _ultimo_ms:
            _ultimo_ms = ahora_ms
            _ultimo_rnd = _semilla_aleatoria()
        else:
            _ultimo_rnd += 1
            if _ultimo_rnd > _MAX_ALEATORIO:
                _ultimo_ms += 1
                _ultimo_rnd = _semilla_aleatoria()
        ts_efectivo = _ultimo_ms
        rnd_efectivo = _ultimo_rnd

    return _codificar_crockford(ts_efectivo, 10) + _codificar_crockford(rnd_efectivo, 16)


def nuevo(prefijo: str) -> str:
    """Genera un id monótono: nuevo('tk') -> 'tk_01J8ZC3AH9...'"""
    return f"{prefijo}_{_ulid()}"
