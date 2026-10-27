"""La cola de curación se escribe con la forma real de cm.curation_queue (KC-CM-07-2/07-3).

cm.curation_queue tiene (id, tenant_id, tipo, payload, estado, ...): no existen columnas
snapshot_id, origen_id, destino_id, confianza ni evidencia. Estas pruebas fijan que el cargador y el
trigger de la base hablan el mismo formato, para que la API de curación lea ambos orígenes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.curation import fabricar_curation_items
from kallicode_codemapping.graph_loader import GraphLoader


def _item() -> object:
    return fabricar_curation_items(
        [
            {
                "definition_id": "def_1",
                "concept_name_en": "Invoice",
                "confidence": 0.6,
                "reasoning": "similar",
            }
        ],
        "tenant-x",
        "snap-1",
    )[0]


def test_to_row_usa_tipo_enlace_y_payload_como_el_trigger() -> None:
    item = _item()
    id_, tenant, tipo, payload_json = item.to_row()  # type: ignore[attr-defined]
    payload = json.loads(payload_json)

    assert id_.startswith("cur_") and tenant == "tenant-x"
    assert tipo == "enlace"  # el CHECK de la tabla no admite 'link'
    assert payload["src_id"] == "def_1" and payload["dst_id"] == "Invoice"
    assert payload["confidence"] == 0.6 and payload["snapshot_id"] == "snap-1"
    assert payload["evidencia"]["motivo"] == "similar"


def test_el_insert_solo_usa_columnas_que_existen_y_no_reabre_resueltos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = GraphLoader(dsn="postgresql://mock", tenant_id="tenant-x")
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    monkeypatch.setattr(GraphLoader, "conn", property(lambda self: conn))

    assert loader.load_curation_queue("snap-1", [_item()]) == 1  # type: ignore[list-item]

    sql, filas = cur.executemany.call_args.args
    assert "INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)" in sql
    for columna_inexistente in ("snapshot_id", "origen_id", "destino_id", "confianza", "evidencia"):
        assert columna_inexistente not in sql
    # Un elemento ya aprobado/rechazado por una persona no vuelve a 'pendiente' al reingestar.
    assert "estado = 'pendiente'" in sql
    assert "estado = EXCLUDED" not in sql
    assert len(filas[0]) == 4
