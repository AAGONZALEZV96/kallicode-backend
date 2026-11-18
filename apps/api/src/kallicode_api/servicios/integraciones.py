"""Tests de conexiones reales con proveedores externos (GitHub, GitLab, Jira, Actions, BD).

Implementación KC-BE-07-1 (cierra BE-F20):
- Verifica scopes y permisos efectivos contra la API/BD real de cada proveedor.
- Mide latencia real en milisegundos.
- Detecta privilegios excesivos en bases de datos de clientes.
"""

from __future__ import annotations

from kallicode_core.errors import AppError
from kallicode_core.logging import log

from .proveedores import PROVEEDORES_SOPORTADOS


async def probar(proveedor: str, config: dict, credenciales: dict) -> dict:
    """Ejecuta el test real del proveedor y devuelve {ok, mensaje, permisos, latencia_ms, ...}."""
    verificador = PROVEEDORES_SOPORTADOS.get(proveedor)
    if verificador is None:
        raise AppError(
            "DEPENDENCIA_NO_DISPONIBLE",
            503,
            f"La prueba real de conexión para {proveedor} no está configurada.",
            {
                "servicio": "integraciones",
                "proveedor": proveedor,
            },
        )

    resultado = await verificador(config or {}, credenciales or {})
    salida = resultado.a_dict()

    if salida["ok"]:
        log.info(
            "integraciones.test_ok",
            proveedor=proveedor,
            permisos=salida["permisos"],
            latencia_ms=salida["latencia_ms"],
        )
    else:
        log.warning(
            "integraciones.test_fallo",
            proveedor=proveedor,
            permisos_excesivos=salida.get("permisos_excesivos", False),
            proveedor_caido=salida.get("proveedor_caido", False),
            latencia_ms=salida["latencia_ms"],
        )

    return salida
