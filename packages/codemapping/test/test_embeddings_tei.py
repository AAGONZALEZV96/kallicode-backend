"""Pruebas de unidad para el cliente TEI y generación determinista de UUIDv5 (KC-ONB-02-2)."""

from __future__ import annotations

import uuid

import httpx
import respx
from kallicode_codemapping.embeddings import (
    TEIEmbeddingClient,
    generar_uuid5_vector,
    preparar_items_vectoriales,
)


def test_uuid5_determinismo_e_idempotencia() -> None:
    """Verifica que mismos inputs produzcan exactamente el mismo UUIDv5 (CM-F14)."""
    tenant = "tenant-alpha"
    layer = "definitions"
    node_ref = "auth.service.authenticate"

    uuid_1 = generar_uuid5_vector(tenant, layer, node_ref)
    uuid_2 = generar_uuid5_vector(tenant, layer, node_ref)

    assert uuid_1 == uuid_2
    assert isinstance(uuid_1, uuid.UUID)


def test_uuid5_aislamiento_multi_tenant() -> None:
    """Verifica que el mismo nodo en dos tenants distintos genere UUIDs diferentes."""
    node_ref = "billing.calculate_tax"
    uuid_tenant_a = generar_uuid5_vector("tenant-a", "code", node_ref)
    uuid_tenant_b = generar_uuid5_vector("tenant-b", "code", node_ref)

    assert uuid_tenant_a != uuid_tenant_b


@respx.mock
def test_tei_client_embed_sync() -> None:
    """Prueba la invocación al endpoint TEI simulando la respuesta de BGE-M3 (1024 dims)."""
    endpoint = "http://localhost:8080/embed"
    mock_vector = [0.1] * 1024
    respx.post(endpoint).mock(return_value=httpx.Response(200, json=[mock_vector]))

    client = TEIEmbeddingClient(base_url="http://localhost:8080")
    vectores = client.embed_texts_sync(["def process_order(): pass"])

    assert len(vectores) == 1
    assert len(vectores[0]) == 1024
    assert vectores[0][0] == 0.1


def test_preparar_items_vectoriales() -> None:
    """Verifica la normalización con hash SHA-256 y UUIDv5."""
    items = [{"node_ref": "sym_1", "text": "def test(): return True", "payload": {}}]
    res = preparar_items_vectoriales(
        tenant_id="tenant-demo",
        layer="code",
        items=items,
    )

    assert len(res) == 1
    assert "contenido_hash" in res[0]
    assert len(res[0]["contenido_hash"]) == 64
