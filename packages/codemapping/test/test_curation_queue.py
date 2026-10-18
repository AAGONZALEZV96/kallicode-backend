"""Pruebas unitarias para persistencia en cm.curation_queue con RLS (KC-CM-07-2)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.curation import (
    CurationItem,
    fabricar_curation_items,
)
from kallicode_codemapping.graph_loader import GraphLoader


# --- TEST 1 ---
def test_fabricar_curation_items_determinismo() -> None:
    raw_links = [
        {
            "definition_id": "def_abc_1",
            "concept_name_en": "AccountingInvoice",
            "confidence": 0.62,
            "reasoning": "Posible similitud con facturación pero términos difieren",
        }
    ]

    items = fabricar_curation_items(
        raw_links=raw_links,
        tenant_id="tenant-acme",
        snapshot_id="snap-001",
    )

    assert len(items) == 1
    item = items[0]
    assert item.tenant_id == "tenant-acme"
    assert item.snapshot_id == "snap-001"
    assert item.origen_id == "def_abc_1"
    assert item.destino_id == "AccountingInvoice"
    assert item.confianza == 0.62
    assert item.estado == "pendiente"
    assert item.id.startswith("cur_")


# --- TEST 2 ---
def test_load_curation_queue_ejecuta_transaccion_con_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = GraphLoader(dsn="postgresql://mock", tenant_id="tenant-xyz")
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    loader._conn = mock_conn
    monkeypatch.setattr(GraphLoader, "conn", property(lambda self: mock_conn))

    item = CurationItem(
        id="cur_123",
        tenant_id="tenant-xyz",
        snapshot_id="snap-999",
        tipo="link",
        origen_id="def_1",
        destino_id="ConceptA",
        confianza=0.55,
        evidencia={"motivo": "baja certeza"},
        estado="pendiente",
        creado_en=datetime.now(UTC),
    )

    total = loader.load_curation_queue(snapshot_id="snap-999", items=[item])

    assert total == 1
    mock_cur.execute.assert_any_call("SET LOCAL app.tenant_id = %s;", ("tenant-xyz",))
    assert mock_cur.executemany.called
    assert mock_conn.commit.called


# --- TEST 3 (Este es el que falta en tu archivo) ---
def test_load_curation_queue_vacio_retorna_cero() -> None:
    loader = GraphLoader(dsn="postgresql://mock", tenant_id="tenant-xyz")
    total = loader.load_curation_queue(snapshot_id="snap-999", items=[])
    assert total == 0
