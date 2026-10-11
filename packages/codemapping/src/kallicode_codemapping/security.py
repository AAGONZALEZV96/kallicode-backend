"""Módulo de seguridad para neutralización de prompt injection y delimitación segura (KC-CM-05-4)."""

from __future__ import annotations

import secrets
from typing import Any


def generar_delimitador(longitud: int = 16) -> str:
    """Genera un nonce criptográfico para encapsular contenido de código no confiable."""
    return f"UNTRUSTED_CONTENT_{secrets.token_hex(longitud)}"


def encapsular_contenido_no_confiable(
    texto: str, delimitador: str | None = None
) -> tuple[str, str]:
    """Envuelve texto/código no confiable dentro de un bloque seguro con delimitador único."""
    delim = delimitador or generar_delimitador()
    # Sanitizar cualquier intento del atacante de cerrar prematuramente el delimitador
    texto_seguro = texto.replace(delim, f"[ESCAPED_{delim}]")
    bloque = f"<{delim}>\n{texto_seguro}\n</{delim}>"
    return bloque, delim


def construir_payload_seguro_simbolo(simbolo: dict[str, Any]) -> dict[str, Any]:
    """Sanitiza y delimita campos no confiables de un símbolo AST (código, firma, snippet)."""
    delim = generar_delimitador()
    snippet = str(simbolo.get("snippet", ""))
    snippet_seguro = snippet.replace(delim, f"[ESCAPED_{delim}]")

    return {
        "id": simbolo.get("id"),
        "name": simbolo.get("name"),
        "kind": simbolo.get("kind"),
        "file": simbolo.get("file"),
        "signature": simbolo.get("signature"),
        "delimiter": delim,
        "snippet": f"<{delim}>\n{snippet_seguro}\n</{delim}>",
    }
