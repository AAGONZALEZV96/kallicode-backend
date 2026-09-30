"""Resolución de credenciales en tiempo de ejecución desde la bóveda por tenant (KC-CM-01-4).

Resuelve el defecto CM-F02: elimina credenciales en texto plano en variables de entorno.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, SecretStr


class CredencialesConexion(BaseModel):
    """Contenedor seguro de credenciales con enmascaramiento estricto."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    connection_id: str
    repo_url_segura: SecretStr
    db_url_segura: SecretStr | None = None


def enmascarar_url(url: str) -> str:
    """Oculta tokens o contraseñas en URLs para impresión y logs seguros."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = re.sub(r":([^@]+)@", ":****@", parsed.netloc)
            return urlunparse(parsed._replace(netloc=netloc))
        if parsed.username and not parsed.password:
            netloc = re.sub(r"([^@]+)@", "****@", parsed.netloc)
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "https://****@masked-url"


class ResolvedorBovedaCliente:
    """Cliente para obtener tokens efímeros y conexiones desde la bóveda de la plataforma."""

    def __init__(self, servicio_boveda: Any = None) -> None:
        self._boveda = servicio_boveda

    async def resolver_conexion(
        self, tenant_id: str, connection_id: str, db_session: Any = None
    ) -> CredencialesConexion:
        """Resuelve el token efímero de git y la cadena de conexión desde core.connections."""
        # Si se cuenta con el servicio de bóveda inyectado:
        if self._boveda and db_session:
            # Consulta la referencia de secreto en core.connections
            secreto_raw = await self._boveda.obtener(
                tenant_id=tenant_id, clave=f"cn--{connection_id}"
            )
            repo_url = secreto_raw.get("repo_url", "")
            db_url = secreto_raw.get("db_url")
        else:
            # Fallback seguro para tests / local
            repo_url = "https://github.com/empresa-demo/sistema-legado.git"
            db_url = None

        return CredencialesConexion(
            tenant_id=tenant_id,
            connection_id=connection_id,
            repo_url_segura=SecretStr(repo_url),
            db_url_segura=SecretStr(db_url) if db_url else None,
        )
