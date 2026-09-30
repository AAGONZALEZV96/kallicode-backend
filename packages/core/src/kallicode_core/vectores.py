"""Capa vectorial: upserts idempotentes sobre vec.* (KC-ONB-02-5).

La migración 001_vec_modelo_embedding_hash.sql agregó `texto_hash` a
vec.doc_chunks y vec.definition_embeddings (vec.ticket_embeddings ya lo
tenía) con un propósito concreto: permitir saltarse el re-embeber cuando
el contenido no cambió, ahorrando una llamada a TEI y una escritura.

Antes de este módulo, esa lógica no existía: normalizer.py sobrescribía
vec.ticket_embeddings en cada corrida de triage.classify sin comparar el
hash. Este módulo centraliza el upsert idempotente para que cualquier
consumidor (workers, futuros endpoints de refresh) lo comparta.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ids import nuevo


async def upsert_embedding_ticket(
    db: AsyncSession,
    *,
    tenant_id: str,
    ticket_id: str,
    embedding: list[float],
    texto_hash: str,
    modelo: str = "bge-m3",
) -> bool:
    """Inserta o actualiza vec.ticket_embeddings de forma idempotente.

    Devuelve True si escribió (fila nueva o contenido cambiado) y False si
    saltó la escritura porque texto_hash coincide con el valor ya
    almacenado (mismo contenido, nada que re-embeber).
    """
    fila = await db.execute(
        text("SELECT texto_hash FROM vec.ticket_embeddings WHERE ticket_id = :tk"),
        {"tk": ticket_id},
    )
    existente = fila.scalar()
    if existente == texto_hash:
        return False

    await db.execute(
        text("""
            INSERT INTO vec.ticket_embeddings
                (ticket_id, tenant_id, embedding, modelo_embedding, texto_hash)
            VALUES (:tk, :t, CAST(:e AS vector), :m, :h)
            ON CONFLICT (ticket_id) DO UPDATE
                SET embedding = CAST(:e AS vector),
                    modelo_embedding = :m,
                    texto_hash = :h
            """),
        {
            "tk": ticket_id,
            "t": tenant_id,
            "e": str(embedding),
            "m": modelo,
            "h": texto_hash,
        },
    )
    return True


async def upsert_definition_embedding(
    db: AsyncSession,
    *,
    tenant_id: str,
    nodo_ref: str,
    capa: str,
    nombre: str,
    descripcion: str,
    embedding: list[float],
    texto_hash: str,
    modulo: str | None = None,
) -> bool:
    """Inserta o actualiza vec.definition_embeddings de forma idempotente.

    Usa la restricción UNIQUE (tenant_id, nodo_ref) para el upsert; igual
    que con tickets, no reescribe la fila si el texto_hash no cambió.
    """
    fila = await db.execute(
        text(
            "SELECT texto_hash FROM vec.definition_embeddings WHERE tenant_id = :t AND nodo_ref = :n"
        ),
        {"t": tenant_id, "n": nodo_ref},
    )
    existente = fila.scalar()
    if existente == texto_hash:
        return False

    await db.execute(
        text("""
            INSERT INTO vec.definition_embeddings
                (id, tenant_id, nodo_ref, capa, nombre, descripcion, modulo,
                 embedding, texto_hash, actualizado_en)
            VALUES (:id, :t, :n, :capa, :nombre, :desc, :mod,
                    CAST(:e AS vector), :h, now())
            ON CONFLICT (tenant_id, nodo_ref) DO UPDATE
                SET capa = :capa,
                    nombre = :nombre,
                    descripcion = :desc,
                    modulo = :mod,
                    embedding = CAST(:e AS vector),
                    texto_hash = :h,
                    actualizado_en = now()
            """),
        {
            "id": nuevo("de"),
            "t": tenant_id,
            "n": nodo_ref,
            "capa": capa,
            "nombre": nombre,
            "desc": descripcion,
            "mod": modulo,
            "e": str(embedding),
            "h": texto_hash,
        },
    )
    return True
