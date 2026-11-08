"""Capa VECTORIAL: Generación de embeddings vía TEI y UUIDs deterministas."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx

from .config import Settings

if TYPE_CHECKING:
    # Solo para tipado: ingest_docs importa este módulo en tiempo de ejecución.
    from .ingest_docs import DocumentChunk

NAMESPACE_KALLICODE = uuid.UUID("a3b8e7c1-2d4e-4f6a-8b9c-0d1e2f3a4b5c")
LAYERS = ("code", "definitions", "docs", "incidents")

DIMENSIONES_BGE_M3 = 1024
MODELO_EMBEDDING = "bge-m3"
# TEI rechaza peticiones con más entradas que --max-client-batch-size (32 por defecto).
TAMANO_LOTE_TEI = 32


class EmbeddingInvalidoError(ValueError):
    """TEI devolvió un número de vectores o una dimensión distinta de la esperada."""


def generar_uuid5_vector(tenant_id: str, layer: str, node_ref: str) -> uuid.UUID:
    """Genera un UUIDv5 determinista combinando tenant_id, capa y la referencia del nodo."""
    identificador_canonico = f"{tenant_id}:{layer}:{node_ref}"
    return uuid.uuid5(NAMESPACE_KALLICODE, identificador_canonico)


def calcular_contenido_hash(texto: str) -> str:
    """Genera el hash SHA-256 del contenido."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def id_doc_chunk(tenant_id: str, documento_id: str, chunk_idx: int) -> str:
    """Id determinista de un chunk de documentación: mismo documento + índice -> mismo id."""
    return str(generar_uuid5_vector(tenant_id, "docs", f"{documento_id}#{chunk_idx}"))


class TEIEmbeddingClient:
    """Cliente HTTP hacia el microservicio Text Embeddings Inference (BGE-M3)."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.timeout = timeout

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Invoca el endpoint /embed de TEI de forma ASÍNCRONA."""
        if not texts:
            return []

        url = f"{self.base_url}/embed"
        payload = {"inputs": texts, "truncate": True}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            vectores: list[list[float]] = response.json()
            return vectores

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        """Invoca el endpoint /embed de TEI de forma SÍNCRONA."""
        if not texts:
            return []

        url = f"{self.base_url}/embed"
        payload = {"inputs": texts, "truncate": True}

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            vectores: list[list[float]] = response.json()
            return vectores


def embeber_en_lotes(
    cliente: TEIEmbeddingClient,
    textos: Sequence[str],
    tamano_lote: int = TAMANO_LOTE_TEI,
    dimensiones: int = DIMENSIONES_BGE_M3,
) -> list[list[float]]:
    """Embebe `textos` en lotes que TEI acepta y valida cantidad y dimensión de cada vector.

    Un vector de otra dimensión no debe llegar a vec.* (la columna es vector(1024) y el
    05_validacion lo cuenta como problema): se corta aquí con EmbeddingInvalidoError.
    """
    if tamano_lote < 1:
        raise ValueError("tamano_lote debe ser >= 1")
    vectores: list[list[float]] = []
    for i in range(0, len(textos), tamano_lote):
        lote = list(textos[i : i + tamano_lote])
        resultado = cliente.embed_texts_sync(lote)
        if len(resultado) != len(lote):
            raise EmbeddingInvalidoError(
                f"TEI devolvio {len(resultado)} vectores para {len(lote)} textos"
            )
        for vector in resultado:
            if len(vector) != dimensiones:
                raise EmbeddingInvalidoError(
                    f"TEI devolvio un vector de {len(vector)} dimensiones; se esperaban {dimensiones}"
                )
        vectores.extend(resultado)
    return vectores


def preparar_items_vectoriales(
    tenant_id: str,
    layer: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepara y normaliza los registros vectoriales con UUIDv5 deterministas y hash SHA-256."""
    preparados: list[dict[str, Any]] = []

    for item in items:
        node_ref = str(item.get("node_ref") or item.get("id"))
        texto = str(item["text"])
        vector_id = generar_uuid5_vector(tenant_id, layer, node_ref)
        hash_c = calcular_contenido_hash(texto)

        preparados.append(
            {
                "id": str(vector_id),
                "tenant_id": tenant_id,
                "layer": layer,
                "node_ref": node_ref,
                "text": texto,
                "contenido_hash": hash_c,
                "payload": item.get("payload", {}),
            }
        )

    return preparados


def embed_and_upsert(
    settings: Settings, layer: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Genera vectores densos vía TEI y estructura los items con UUIDv5 deterministas."""
    if not items:
        return []

    client = TEIEmbeddingClient(base_url=settings.tei_endpoint_url)
    textos = [str(item["text"]) for item in items]
    vectores = client.embed_texts_sync(textos)

    tenant = settings.tenant_id
    resultados: list[dict[str, Any]] = []

    for item, texto, vector in zip(items, textos, vectores, strict=True):
        node_ref = str(item.get("node_ref") or item.get("id"))
        vector_id = generar_uuid5_vector(tenant, layer, node_ref)
        resultados.append(
            {
                "id": str(vector_id),
                "tenant_id": tenant,
                "layer": layer,
                "node_ref": node_ref,
                "text": texto,
                "vector": vector,
                "contenido_hash": calcular_contenido_hash(texto),
                "payload": item.get("payload", {}),
            }
        )

    return resultados


def preparar_items_doc_chunks(
    chunks: list[DocumentChunk],
    vectores: list[list[float]],
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Empaqueta chunks de documentación con sus vectores y UUIDv5 deterministas para vec.doc_chunks."""
    preparados: list[dict[str, Any]] = []

    for chunk, vector in zip(chunks, vectores, strict=True):
        preparados.append(
            {
                "id": id_doc_chunk(tenant_id, chunk.documento_id, chunk.chunk_idx),
                "tenant_id": tenant_id,
                "documento_id": chunk.documento_id,
                "chunk_idx": chunk.chunk_idx,
                "seccion": chunk.seccion,
                "texto": chunk.texto,
                "vector": vector,
                "modelo_embedding": MODELO_EMBEDDING,
                "contenido_hash": chunk.contenido_hash,
                "metadata": chunk.metadata,
                "tipo_doc": chunk.tipo_doc,
            }
        )

    return preparados
