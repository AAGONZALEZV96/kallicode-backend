"""Tests de búsqueda por título/descripción y dedup (KC-BE-07-4)."""

from __future__ import annotations

import json
import math
import uuid

import pytest
from kallicode_core.db import sesion_sistema, uno
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _hay_indice_trgm_descripcion() -> bool:
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT 1 AS ok
              FROM pg_indexes
             WHERE schemaname = 'core'
               AND tablename = 'tickets'
               AND indexdef ILIKE '%gin%'
               AND indexdef ILIKE '%descripcion%'
            """,
        )
    return fila is not None


def _no_seq_scan(nodo: dict) -> bool:
    if nodo.get("Node Type") == "Seq Scan":
        if nodo.get("Relation Name") == "tickets":
            return False

    return all(_no_seq_scan(hijo) for hijo in nodo.get("Plans", []))


async def test_busqueda_no_hace_seq_scan(
    seed_50k_tickets,
    tenant_id,
):
    if not await _hay_indice_trgm_descripcion():
        pytest.skip("Falta el índice GIN trgm sobre descripcion (KC-DB-06-2)")

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            EXPLAIN (FORMAT JSON)
            SELECT numero
              FROM core.tickets
             WHERE tenant_id = :t
               AND (titulo ILIKE :q OR descripcion ILIKE :q)
             LIMIT 25
            """,
            {
                "t": tenant_id,
                "q": "%login%",
            },
        )

    plan = json.loads(fila["QUERY PLAN"])[0]["Plan"]
    assert _no_seq_scan(plan), f"Plan con Seq Scan: {plan}"


@pytest.fixture
async def seed_50k_tickets(tenant_id):
    """Siembra 50.000 tickets en bulk cuando el driver lo permite."""
    async with sesion_sistema() as db:
        try:
            conexion = await db.connection()
            raw = await conexion.get_raw_connection()
            driver = raw.driver_connection

            await driver.copy_records_to_table(
                "tickets",
                schema_name="core",
                records=[
                    (
                        f"tk_seed_{i:06d}",
                        tenant_id,
                        f"KC-{i:06d}",
                        "bug",
                        f"Ticket seed {i}",
                        f"Descripcion seed {i} con login y sesión",
                        "media",
                        "triage",
                        "portal",
                    )
                    for i in range(50_000)
                ],
                columns=[
                    "id",
                    "tenant_id",
                    "numero",
                    "tipo",
                    "titulo",
                    "descripcion",
                    "prioridad",
                    "etapa",
                    "origen",
                ],
            )
            await db.commit()
        except AttributeError:
            pytest.skip("El driver actual no expone copy_records_to_table")

    yield tenant_id

    async with sesion_sistema() as db:
        await db.execute(
            text("DELETE FROM core.tickets WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        await db.commit()


@pytest.mark.parametrize(
    "similitud,esperado",
    [
        (0.50, False),
        (0.65, False),
        (0.69, False),
        (0.70, True),
        (0.75, True),
    ],
)
async def test_dedup_preview_umbral(
    client,
    auth_headers_member,
    tenant_id,
    similitud,
    esperado,
    monkeypatch_embebido,
):
    ticket_id = f"tk_{uuid.uuid4().hex[:8]}"
    numero = f"KC-{uuid.uuid4().hex[:8]}"

    componente = math.sqrt(max(0.0, 1.0 - similitud**2))
    vector_existente = [1.0] + [0.0] * 1023
    vector_consulta = [similitud, componente] + [0.0] * 1022

    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.tickets
                    (id, tenant_id, numero, tipo, titulo, descripcion,
                     prioridad, etapa, origen)
                VALUES
                    (:i, :t, :n, 'bug', 'Ticket similar',
                     'Desc', 'media', 'triage', 'portal')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "n": numero,
            },
        )

        await db.execute(
            text("""
                INSERT INTO vec.ticket_embeddings
                    (ticket_id, tenant_id, embedding, texto_hash)
                VALUES
                    (:i, :t, CAST(:emb AS vector), 'hash')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "emb": str(vector_existente),
            },
        )
        await db.commit()

    monkeypatch_embebido.vector = vector_consulta

    respuesta = await client.post(
        "/api/v1/tickets/dedup-preview",
        json={
            "texto": "Ticket que se parece al existente",
            "k": 5,
        },
        headers=auth_headers_member,
    )

    assert respuesta.status_code == 200, respuesta.text
    items = respuesta.json()["similares"]
    encontrado = any(item["ticket_id"] == numero for item in items)

    assert encontrado is esperado


async def test_crear_devuelve_duplicado_potencial(
    client,
    auth_headers_member,
    tenant_id,
    monkeypatch_embebido,
):
    ticket_id = f"tk_{uuid.uuid4().hex[:8]}"
    numero = f"KC-{uuid.uuid4().hex[:8]}"
    vector = [1.0] + [0.0] * 1023

    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.tickets
                    (id, tenant_id, numero, tipo, titulo, descripcion,
                     prioridad, etapa, origen)
                VALUES
                    (:i, :t, :n, 'bug', 'Login roto',
                     'El formulario devuelve 500',
                     'media', 'triage', 'portal')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "n": numero,
            },
        )

        await db.execute(
            text("""
                INSERT INTO vec.ticket_embeddings
                    (ticket_id, tenant_id, embedding, texto_hash)
                VALUES
                    (:i, :t, CAST(:emb AS vector), 'hash')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "emb": str(vector),
            },
        )
        await db.commit()

    monkeypatch_embebido.vector = vector

    respuesta = await client.post(
        "/api/v1/tickets",
        json={
            "tipo": "bug",
            "titulo": "Login roto al entrar",
            "descripcion": "El formulario devuelve 500 al meter credenciales válidas",
            "prioridad": "alta",
        },
        headers=auth_headers_member,
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["duplicado_potencial"] == numero

    async with sesion_sistema() as db:
        persistido = await uno(
            db,
            """
            SELECT e.texto_hash
              FROM vec.ticket_embeddings e
              JOIN core.tickets t ON t.id = e.ticket_id
             WHERE t.tenant_id = :t
               AND t.numero = :n
            """,
            {
                "t": tenant_id,
                "n": respuesta.json()["id"],
            },
        )

    assert persistido is not None
    assert persistido["texto_hash"]


async def test_crear_sin_duplicado_si_similitud_baja(
    client,
    auth_headers_member,
    tenant_id,
    monkeypatch_embebido,
):
    ticket_id = f"tk_{uuid.uuid4().hex[:8]}"
    numero = f"KC-{uuid.uuid4().hex[:8]}"

    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.tickets
                    (id, tenant_id, numero, tipo, titulo, descripcion,
                     prioridad, etapa, origen)
                VALUES
                    (:i, :t, :n, 'bug', 'Otra cosa',
                     'Nada que ver', 'media', 'triage', 'portal')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "n": numero,
            },
        )

        await db.execute(
            text("""
                INSERT INTO vec.ticket_embeddings
                    (ticket_id, tenant_id, embedding, texto_hash)
                VALUES
                    (:i, :t,
                     CAST(:emb AS vector),
                     'hash')
            """),
            {
                "i": ticket_id,
                "t": tenant_id,
                "emb": str([1.0] + [0.0] * 1023),
            },
        )
        await db.commit()

    monkeypatch_embebido.vector = [0.0, 1.0] + [0.0] * 1022

    respuesta = await client.post(
        "/api/v1/tickets",
        json={
            "tipo": "bug",
            "titulo": "Otro ticket distinto",
            "descripcion": "No se parece al anterior",
            "prioridad": "media",
        },
        headers=auth_headers_member,
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["duplicado_potencial"] is None
