"""Pruebas unitarias para ingesta y troceado documental en vec.doc_chunks (KC-CM-08-1)."""

from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.embeddings import preparar_items_doc_chunks
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.ingest_docs import (
    clasificar_tipo_documento,
    trocear_documento_markdown,
)


def test_clasificar_tipo_documento() -> None:
    assert clasificar_tipo_documento("docs/adr/ADR-018.md", "") == "adr"
    assert clasificar_tipo_documento("spec_tecnica.md", "Requirements doc") == "spec"
    assert clasificar_tipo_documento("manual_usuario.md", "") == "manual"
    assert clasificar_tipo_documento("politicas_negocio.txt", "") == "negocio"
    assert clasificar_tipo_documento("random.txt", "texto normal") == "general"


def test_troceado_markdown_respeta_encabezados() -> None:
    contenido = """# Titulo General
Este es el contexto de introducción.

## Sección 1: Pagos
Detalles del flujo de pago y comisiones.

## Sección 2: Facturación
Explicación del motor de facturación electrónica.
"""
    chunks = trocear_documento_markdown(
        documento_id="doc_001",
        contenido=contenido,
        ruta="docs/pagos.md",
        tamano_max_caracteres=200,
    )

    assert len(chunks) >= 3
    secciones = [c.seccion for c in chunks]
    assert "Sección 1: Pagos" in secciones
    assert "Sección 2: Facturación" in secciones
    # Cada chunk contiene su hash de contenido
    for c in chunks:
        assert len(c.contenido_hash) == 64
        assert c.documento_id == "doc_001"


def test_preparar_items_doc_chunks_uuid5_determinista() -> None:
    chunks = trocear_documento_markdown("doc_1", "## Regla\nTexto de prueba")
    vectores = [[0.1] * 1024 for _ in chunks]

    items = preparar_items_doc_chunks(chunks, vectores, tenant_id="tenant-alpha")
    assert len(items) == len(chunks)
    assert items[0]["modelo_embedding"] == "bge-m3"
    assert items[0]["tenant_id"] == "tenant-alpha"
    assert items[0]["id"] is not None


def test_load_doc_chunks_persistencia_con_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = GraphLoader(dsn="postgresql://mock", tenant_id="tenant-doc")
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    loader._conn = mock_conn
    monkeypatch.setattr(GraphLoader, "conn", property(lambda self: mock_conn))

    items = [
        {
            "id": "uuid-doc-1",
            "tenant_id": "tenant-doc",
            "documento_id": "doc_001",
            "chunk_idx": 0,
            "seccion": "Arquitectura",
            "texto": "Texto del fragmento",
            "vector": [0.05] * 1024,
            "modelo_embedding": "bge-m3",
            "contenido_hash": "a" * 64,
            "metadata": {},
            "tipo_doc": "adr",
        }
    ]

    total = loader.load_doc_chunks(items)
    assert total == 1
    mock_cur.execute.assert_any_call("SET LOCAL app.tenant_id = %s;", ("tenant-doc",))
    assert mock_cur.executemany.called
    assert mock_conn.commit.called
