"""Blob storage: URLs firmadas de subida y descarga.

Local: URLs pseudo-firmadas contra Azurite (o dummy). El contrato es el
mismo que en producción: nunca se sirve el binario a través de la API.
TODO(produccion): generar SAS reales con azure-storage-blob + user
delegation key (managed identity).
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from kallicode_core.config import get_settings

_CLAVE_DEV = b"kc-dev-firma"


def _firmar(ref: str, exp: datetime, verbo: str) -> str:
    base = f"{verbo}:{ref}:{int(exp.timestamp())}"
    return hmac.new(_CLAVE_DEV, base.encode(), hashlib.sha256).hexdigest()[:32]


def url_subida(ref: str, content_type: str, minutos: int = 15) -> tuple[str, str]:
    """Devuelve (upload_url, expira_en ISO). PUT directo del cliente al blob."""
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutos)
    firma = _firmar(ref, exp, "put")
    url = (f"{s.blob_account_url}/{s.blob_container_evidencia}/{ref}"
           f"?sv=dev&se={int(exp.timestamp())}&sig={firma}")
    return url, exp.isoformat()


def url_descarga(ref: str, minutos: int = 10, horas: int | None = None) -> tuple[str, str]:
    """Devuelve (download_url, expira_en ISO) de solo lectura."""
    s = get_settings()
    delta = timedelta(hours=horas) if horas else timedelta(minutes=minutos)
    exp = datetime.now(timezone.utc) + delta
    firma = _firmar(ref, exp, "get")
    url = (f"{s.blob_account_url}/{s.blob_container_evidencia}/{ref}"
           f"?sv=dev&se={int(exp.timestamp())}&sig={firma}")
    return url, exp.isoformat()
