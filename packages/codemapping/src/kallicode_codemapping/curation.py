"""Gestión de elementos y serialización para cm.curation_queue (KC-CM-07-2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class CurationItem:
    """Representa un elemento candidato retenido para revisión humana."""

    id: str
    tenant_id: str
    snapshot_id: str
    tipo: str  # 'link' | 'concept'
    origen_id: str
    destino_id: str
    confianza: float
    evidencia: dict[str, Any] = field(default_factory=dict)
    estado: str = "pendiente"
    creado_en: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_tuple(self) -> tuple[str, str, str, str, str, str, float, str, str, datetime]:
        """Convierte la entidad a tupla para inserción o streaming COPY."""
        return (
            self.id,
            self.tenant_id,
            self.snapshot_id,
            self.tipo,
            self.origen_id,
            self.destino_id,
            self.confianza,
            json.dumps(self.evidencia),
            self.estado,
            self.creado_en,
        )


def fabricar_curation_items(
    raw_links: list[dict[str, Any]],
    tenant_id: str,
    snapshot_id: str,
) -> list[CurationItem]:
    """Transforma los enlaces rechazados por bajo umbral en entidades CurationItem tipadas."""
    items: list[CurationItem] = []

    for item in raw_links:
        origen = str(item.get("definition_id") or item.get("symbol_id") or "")
        destino = str(item.get("concept_name_en") or item.get("concept_id") or "")
        confianza = float(item.get("confidence") or 0.0)

        # Generar ID determinista por tenant, snapshot y arista
        raw_seed = f"{tenant_id}:{snapshot_id}:{origen}:{destino}"
        hash_id = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:16]
        item_id = f"cur_{hash_id}"

        evidencia = {
            "raw_link": item,
            "motivo": item.get("reasoning", "Confianza inferior al umbral del tenant"),
        }

        items.append(
            CurationItem(
                id=item_id,
                tenant_id=tenant_id,
                snapshot_id=snapshot_id,
                tipo="link",
                origen_id=origen,
                destino_id=destino,
                confianza=confianza,
                evidencia=evidencia,
                estado="pendiente",
            )
        )

    return items
