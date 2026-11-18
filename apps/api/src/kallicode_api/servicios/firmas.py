"""Verificación criptográfica de webhooks, anti-replay, límites por proveedor y rotación (KC-BE-07-2)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from kallicode_core.config import get_settings
from kallicode_core.errors import AppError
from kallicode_core.logging import log

# Límites de tamaño de cuerpo por proveedor (bytes).
# Kallicode Help admite un límite mayor (5 MB) por incluir evidencia adjunta.
LIMITES_PAYLOAD_BYTES: dict[str, int] = {
    "github": 1_048_576,  # 1 MB
    "gitlab": 1_048_576,  # 1 MB
    "jira": 1_048_576,  # 1 MB
    "ci": 1_048_576,  # 1 MB
    "github_actions": 1_048_576,  # 1 MB
    "corestream": 1_048_576,  # 1 MB
    "help": 4_194_304,  # 5 MB (evidencia adjunta)
    "kallicode_help": 4_194_304,  # 5 MB
}
_LIMITE_DEFAULT_BYTES = 1_048_576


def limite_payload_para(proveedor: str) -> int:
    return LIMITES_PAYLOAD_BYTES.get(proveedor, _LIMITE_DEFAULT_BYTES)


def validar_tamano_payload(proveedor: str, cuerpo: bytes) -> None:
    limite = limite_payload_para(proveedor)
    if len(cuerpo) > limite:
        raise AppError(
            "PAYLOAD_EXCESIVO",
            413,
            f"El payload supera el tamaño permitido para {proveedor} ({limite} bytes).",
            {
                "proveedor": proveedor,
                "max_bytes": limite,
                "recibido_bytes": len(cuerpo),
            },
        )


def parsear_timestamp_webhook(valor_raw: str | None) -> datetime | None:
    """Convierte una cabecera de timestamp (epoch segundos/ms o ISO-8601) a datetime UTC."""
    if not valor_raw or not str(valor_raw).strip():
        return None
    texto = str(valor_raw).strip()

    # Soportar formato compuesto tipo 't=1731880000,v1=abcdef...'
    if "t=" in texto and "," in texto:
        for parte in texto.split(","):
            parte = parte.strip()
            if parte.startswith("t="):
                texto = parte[2:].strip()
                break

    try:
        numerico = float(texto)
        # Si viene en milisegundos (> 10^12), normalizar a segundos
        if numerico > 1_000_000_000_000:
            numerico /= 1000.0
        return datetime.fromtimestamp(numerico, tz=UTC)
    except ValueError:
        pass

    try:
        dt = datetime.fromisoformat(texto)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def validar_ventana_temporal(
    timestamp_raw: str | None,
    *,
    ahora: datetime | None = None,
    ventana_s: int | None = None,
) -> datetime:
    """Valida que la entrega esté dentro de la ventana anti-replay configurable.

    Rechaza marcas ausentes, malformadas, expiradas o futuras fuera de tolerancia con
    401 FIRMA_INVALIDA de cuerpo mínimo.
    """
    dt_entrega = parsear_timestamp_webhook(timestamp_raw)
    if dt_entrega is None:
        raise AppError("FIRMA_INVALIDA", 401, "")

    referencia = ahora or datetime.now(UTC)
    tolerancia = ventana_s if ventana_s is not None else get_settings().webhook_replay_window_s
    desfase_s = abs((referencia - dt_entrega).total_seconds())

    if desfase_s > tolerancia:
        raise AppError("FIRMA_INVALIDA", 401, "")

    return dt_entrega


def extraer_secretos_vigentes(
    secreto_boveda: dict[str, Any],
    *,
    ahora: datetime | None = None,
) -> list[str]:
    """Devuelve los secretos válidos de una conexión (actual + anterior si no expiró)."""
    referencia = ahora or datetime.now(UTC)
    vigentes: list[str] = []

    actual = (
        secreto_boveda.get("webhook_secret")
        or secreto_boveda.get("api_token")
        or secreto_boveda.get("token")
    )
    if actual:
        vigentes.append(str(actual))

    anterior = secreto_boveda.get("webhook_secret_anterior")
    expira_raw = secreto_boveda.get("webhook_secret_anterior_expira_en")
    if anterior and expira_raw:
        dt_expira = parsear_timestamp_webhook(str(expira_raw))
        if dt_expira is not None and referencia <= dt_expira:
            if str(anterior) not in vigentes:
                vigentes.append(str(anterior))

    return vigentes


def rotar_secreto_boveda(
    secreto_actual_boveda: dict[str, Any] | None,
    *,
    nuevo_secreto: str | None = None,
    ventana_convivencia_s: int | None = None,
    ahora: datetime | None = None,
) -> tuple[dict[str, Any], str, datetime]:
    """Prepara el nuevo diccionario de bóveda con convivencia temporal del secreto anterior."""
    referencia = ahora or datetime.now(UTC)
    gracia_s = (
        ventana_convivencia_s
        if ventana_convivencia_s is not None
        else get_settings().webhook_secret_rotation_grace_s
    )
    expira_en = referencia + timedelta(seconds=gracia_s)

    datos = dict(secreto_actual_boveda or {})
    previo = datos.get("webhook_secret") or datos.get("api_token") or datos.get("token")
    secreto_nuevo = (nuevo_secreto or "").strip() or f"whsec_{secrets.token_urlsafe(32)}"

    datos["webhook_secret"] = secreto_nuevo
    if previo and str(previo) != secreto_nuevo:
        datos["webhook_secret_anterior"] = str(previo)
        datos["webhook_secret_anterior_expira_en"] = expira_en.isoformat()

    return datos, secreto_nuevo, expira_en


def firmar_payload(secreto: str, cuerpo: bytes, timestamp_raw: str | None = None) -> str:
    """Calcula la firma HMAC-SHA256 esperada (con prefijo sha256=)."""
    mensaje = (
        f"{timestamp_raw}.".encode() + cuerpo
        if timestamp_raw is not None and str(timestamp_raw).strip() != ""
        else cuerpo
    )
    digest = hmac.new(secreto.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _extraer_firma_hex(firma_header: str) -> str:
    valor = firma_header.strip()
    if "v1=" in valor and "," in valor:
        for parte in valor.split(","):
            parte = parte.strip()
            if parte.startswith("v1="):
                return parte[3:].strip()
    return valor.removeprefix("sha256=").strip()


def verificar_firma_entrega(
    *,
    proveedor: str,
    cuerpo: bytes,
    firma_header: str | None,
    timestamp_header: str | None,
    secretos_vigentes: list[str],
    ahora: datetime | None = None,
) -> None:
    """Verifica marca temporal anti-replay y firma HMAC contra todos los secretos vigentes."""
    if not firma_header or not secretos_vigentes:
        log.warning("webhooks.firma_ausente_o_sin_secreto", proveedor=proveedor)
        raise AppError("FIRMA_INVALIDA", 401, "")

    # 1. Validar ventana anti-replay (si el header trae t=... y timestamp_header está vacío, usarlo)
    ts_efectivo = timestamp_header
    if not ts_efectivo and "t=" in firma_header:
        ts_efectivo = firma_header

    validar_ventana_temporal(ts_efectivo, ahora=ahora)

    # 2. Verificar firma HMAC en tiempo constante contra secretos vigentes (actual + anterior)
    recibida = _extraer_firma_hex(firma_header)
    ts_limpio = str(ts_efectivo).strip()

    for clave in secretos_vigentes:
        # Soportar firma ligada al timestamp ("<ts>.<body>") y firma estándar sobre cuerpo ("<body>")
        esperada_con_ts = hmac.new(
            clave.encode("utf-8"),
            f"{ts_limpio}.".encode() + cuerpo,
            hashlib.sha256,
        ).hexdigest()
        esperada_cuerpo = hmac.new(
            clave.encode("utf-8"),
            cuerpo,
            hashlib.sha256,
        ).hexdigest()

        if hmac.compare_digest(esperada_con_ts, recibida) or hmac.compare_digest(
            esperada_cuerpo, recibida
        ):
            return

        # GitLab puede enviar el token secreto directo en X-Gitlab-Token
        if proveedor == "gitlab" and hmac.compare_digest(clave, firma_header.strip()):
            return

    log.warning("webhooks.firma_invalida", proveedor=proveedor)
    raise AppError("FIRMA_INVALIDA", 401, "")
