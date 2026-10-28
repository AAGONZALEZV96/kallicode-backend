"""Normalización y saneo de elementos de cm.curation_queue para la API de curación (KC-CM-07-3/5).

La cola la alimentan dos orígenes con la misma forma de `payload`: el trigger de la base
(`cm.edges_confianza_trg`, `cm.concepts_confianza_trg`) y el cargador de CodeMapping
(`GraphLoader.load_curation_queue`). El detalle (razonamiento, nombres) lo produce un LLM a partir
de código del cliente, así que es texto no confiable: aquí se limpia antes de salir por la API
(KC-CM-07-4) y la UI debe pintarlo siempre como texto, nunca como HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any

ESTADOS = ("pendiente", "aprobado", "rechazado")
TIPOS = ("concepto", "enlace", "nugget")
DECISIONES = ("aprobado", "rechazado")
MAX_TEXTO = 2000

_PELIGROSOS = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]"
)


def sanear_texto(valor: str, max_len: int = MAX_TEXTO) -> str:
    """Quita caracteres de control, de ancho cero y de reordenamiento bidireccional; acota."""
    return _PELIGROSOS.sub("", valor)[:max_len]


def sanear_json(valor: Any, profundidad: int = 0) -> Any:
    """Sanea recursivamente todas las cadenas de un JSON (valores y claves)."""
    if profundidad > 8:
        return None
    if isinstance(valor, str):
        return sanear_texto(valor)
    if isinstance(valor, dict):
        return {
            sanear_texto(str(k), 100): sanear_json(v, profundidad + 1) for k, v in valor.items()
        }
    if isinstance(valor, list):
        return [sanear_json(v, profundidad + 1) for v in valor[:200]]
    return valor


def _payload(fila: dict[str, Any]) -> dict[str, Any]:
    bruto = fila.get("payload")
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            bruto = {}
    return bruto if isinstance(bruto, dict) else {}


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    return float(valor)


def normalizar_item(fila: dict[str, Any], con_payload: bool = False) -> dict[str, Any]:
    """Convierte una fila de cm.curation_queue en el objeto que expone la API."""
    p = _payload(fila)
    es_concepto = fila.get("tipo") == "concepto"
    confianza = _numero(p.get("confidence"))
    umbral = _numero(p.get("umbral"))
    evidencia = p.get("evidencia") if isinstance(p.get("evidencia"), dict) else {}

    motivo = evidencia.get("motivo")
    if not isinstance(motivo, str) or not motivo:
        if confianza is not None and umbral is not None:
            motivo = f"Confianza {confianza:.2f} inferior al umbral {umbral:.2f}"
        else:
            motivo = "Confianza inferior al umbral del tenant"

    item: dict[str, Any] = {
        "id": fila["id"],
        "tipo": fila["tipo"],
        "estado": fila["estado"],
        "origen": str(p.get("id") if es_concepto else p.get("src_id") or ""),
        "destino": str(p.get("name_en") if es_concepto else p.get("dst_id") or ""),
        "confianza": confianza,
        "umbral": umbral,
        "snapshot_id": p.get("snapshot_id"),
        "motivo": sanear_texto(motivo, 500),
        "revisado_por": fila.get("revisado_por"),
        "revisado_en": fila.get("revisado_en"),
        "creado_en": fila.get("creado_en"),
    }
    for campo in ("origen", "destino"):
        item[campo] = sanear_texto(item[campo], 300)
    if con_payload:
        item["payload"] = sanear_json(p)
    return item
