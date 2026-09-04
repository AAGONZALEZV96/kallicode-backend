from typing import Any
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from .config import settings

LAYERS = ("code", "definitions", "docs", "incidents")
_model: BGEM3FlagModel | None = None


def model() -> BGEM3FlagModel:
    global _model
    if _model is None:
        _model = BGEM3FlagModel(settings.embedding_model, use_fp16=True)
    return _model


def collection_name(layer: str) -> str:
    return f"{settings.client_id}_{layer}"


def ensure_collections(qdrant: QdrantClient) -> None:
    for layer in LAYERS:
        name = collection_name(layer)
        if not qdrant.collection_exists(name):
            qdrant.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
            )


def embed_and_upsert(layer: str, items: list[dict[str, Any]]) -> None:
    qdrant = QdrantClient(url=settings.qdrant_url)
    ensure_collections(qdrant)
    for i in range(0, len(items), settings.batch_size):
        batch = items[i : i + settings.batch_size]
        dense = model().encode([b["text"] for b in batch], batch_size=len(batch))["dense_vecs"]
        points = [
            PointStruct(id=b["id"], vector=v.tolist(), payload=b["payload"])
            for b, v in zip(batch, dense, strict=True)
        ]
        qdrant.upsert(collection_name(layer), points=points)