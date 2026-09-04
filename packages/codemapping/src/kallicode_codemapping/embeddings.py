"""Fase 5 - capa VECTORIAL: BGE-M3 -> Qdrant, una coleccion por capa.

BGE-M3: open source, multilingue (la ontologia habla el idioma del
cliente), 1024 dimensiones, auto-hospedable - coherente con soberania.
Colecciones separadas por capa y por cliente, como en el visor del grafo.
"""
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import settings

LAYERS = ("code", "definitions", "docs", "incidents")
_model = None


def model() -> BGEM3FlagModel:
    global _model
    if _model is None:
        _model = BGEM3FlagModel(settings.embedding_model, use_fp16=True)
    return _model


def collection_name(layer: str) -> str:
    return f"{settings.client_id}_{layer}"      # p. ej. clienteB_definitions


def ensure_collections(qdrant: QdrantClient):
    for layer in LAYERS:
        name = collection_name(layer)
        if not qdrant.collection_exists(name):
            qdrant.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=settings.embedding_dim,
                                            distance=Distance.COSINE))


def embed_and_upsert(layer: str, items: list):
    """items: [{"id": int|uuid, "text": str, "payload": dict}].
    El payload (modulo, tipo de nodo, archivo) habilita el filtrado
    de Qdrant en las consultas de los agentes."""
    qdrant = QdrantClient(url=settings.qdrant_url)
    ensure_collections(qdrant)
    for i in range(0, len(items), settings.batch_size):
        batch = items[i:i + settings.batch_size]
        dense = model().encode([b["text"] for b in batch],
                               batch_size=len(batch))["dense_vecs"]
        points = [PointStruct(id=b["id"], vector=v.tolist(),
                              payload=b["payload"])
                  for b, v in zip(batch, dense)]
        qdrant.upsert(collection_name(layer), points=points)
