"""Tipos y utilidades compartidas para verificación real de proveedores."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx


@dataclass
class ResultadoPrueba:
    ok: bool
    mensaje: str
    permisos: list[str] = field(default_factory=list)
    latencia_ms: int = 0
    permisos_excesivos: bool = False
    proveedor_caido: bool = False

    def a_dict(self) -> dict:
        data: dict = {
            "ok": self.ok,
            "mensaje": self.mensaje,
            "permisos": self.permisos,
            "latencia_ms": self.latencia_ms,
        }
        if self.permisos_excesivos:
            data["permisos_excesivos"] = True
        if self.proveedor_caido:
            data["proveedor_caido"] = True
        return data


def medir_ms(inicio: float) -> int:
    return max(1, round((time.perf_counter() - inicio) * 1000))


def crear_cliente_http(
    *,
    base_url: str = "",
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout_s: float = 10.0,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers or {},
        auth=auth,
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=True,
    )
