"""Capa VECTORIAL: Generación de embeddings vía TEI y UUIDs deterministas."""

from __future__ import annotations

import hashlib
from typing import Any
import uuid

import httpx

from .config import settings

NAMESPACE_KALLICODE = uuid.UUID("a3b8e7c1-2d4e-4f6a-8b9c-0d1e2f3a4b5c")
LAYERS = ("code", "definitions", "docs", "incidents")


def generar_uuid5_vector(tenant_id: str, layer: str, node_ref: str) -> uuid.UUID:
    """Genera un UUIDv5 determinista combinando tenant_id, capa y la referencia del nodo."""
    identificador_canonico = f"{tenant_id}:{layer}:{node_ref}"
    return uuid.uuid5(NAMESPACE_KALLICODE, identificador_canonico)


def calcular_contenido_hash(texto: str) -> str:
    """Genera el hash SHA-256 del contenido."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class TEIEmbeddingClient:
    """Cliente HTTP hacia el microservicio Text Embeddings Inference (BGE-M3)."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        raw_url = base_url if base_url is not None else getattr(settings, "tei_endpoint_url", "http://localhost:8080")
        self.base_url: str = str(raw_url).rstrip("/")
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


def preparar_items_vectoriales(
    layer: str,
    items: list[dict[str, Any]],
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Prepara y normaliza los registros vectoriales con UUIDv5 deterministas y hash SHA-256."""
    raw_tenant = tenant_id or getattr(settings, "tenant_id", "default_tenant")
    tenant: str = str(raw_tenant)
    preparados: list[dict[str, Any]] = []

    for item in items:
        node_ref = str(item.get("node_ref") or item.get("id"))
        texto = str(item["text"])
        vector_id = generar_uuid5_vector(tenant, layer, node_ref)
        hash_c = calcular_contenido_hash(texto)

        preparados.append(
            {
                "id": str(vector_id),
                "tenant_id": tenant,
                "layer": layer,
                "node_ref": node_ref,
                "text": texto,
                "contenido_hash": hash_c,
                "payload": item.get("payload", {}),
            }
        )

    return preparados


def embed_and_upsert(layer: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Genera vectores densos vía TEI y estructura los items con UUIDv5 deterministas."""
    if not items:
        return []

    client = TEIEmbeddingClient()
    textos = [str(item["text"]) for item in items]
    vectores = client.embed_texts_sync(textos)

    raw_tenant = getattr(settings, "tenant_id", "default_tenant")
    tenant: str = str(raw_tenant)
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