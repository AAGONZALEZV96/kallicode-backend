"""Pruebas de integración para el contrato interno de ingesta del grafo (KC-BE-09-6).

Verifica:
1. Escritura convergente por lotes (`POST /internal/v1/codemapping/grafo/batch`): escribir el mismo
   lote dos veces deja exactamente los mismos nodos, aristas y embeddings (idempotente).
2. Aislamiento cross-tenant: intentar escribir un nodo de otro tenant (ya sea declarando un
   `tenant_id` distinto o intentando reutilizar/enlazar un ID perteneciente a otro tenant)
   responde 403 Forbidden sin mutar la base de datos.
3. Estado de ingesta (`GET /internal/v1/codemapping/ingesta/status`): expone fase, progreso,
   cobertura del grafo, curación pendiente y último refresco.
4. Apertura de escalación (`POST /internal/v1/codemapping/escalations`): idempotente con
   `Idempotency-Key` (201 al abrir, 200 al reintentar) y confinada al tenant.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_svc_token
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


def _cabeceras_ingesta(tenant_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_svc_token('ingesta', tenant_id)}",
        "Idempotency-Key": key,
    }


def _vector_unitario(seed: float = 0.01) -> list[float]:
    vec = [0.0] * 1024
    vec[0] = 1.0
    vec[1] = seed
    return vec


@pytest_asyncio.fixture(autouse=True)
async def _limpiar_grafo_y_claves(seed_dos_tenants):
    id_a, id_b = seed_dos_tenants
    yield
    async with sesion_sistema() as db:
        for tabla in (
            "vec.definition_embeddings",
            "cm.edges",
            "cm.curation_queue",
            "cm.concepts",
            "cm.definitions",
            "cm.db_columns",
            "cm.db_tables",
            "cm.symbols",
            "cm.files",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
            "core.idempotency_keys",
        ):
            await db.execute(
                text(f"DELETE FROM {tabla} WHERE tenant_id IN (:a, :b)"),
                {"a": id_a, "b": id_b},
            )
        await db.commit()


async def _snapshot_grafo_db(tenant_id: str) -> dict[str, Any]:
    async with sesion_sistema() as db:
        nodos = (
            (
                await db.execute(
                    text("SELECT id, kind FROM cm.nodes WHERE tenant_id = :t ORDER BY id"),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        aristas = (
            (
                await db.execute(
                    text(
                        "SELECT tipo, src_kind, src_id, dst_kind, dst_id, props "
                        "FROM cm.edges WHERE tenant_id = :t ORDER BY tipo, src_id, dst_id"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        embeddings = (
            (
                await db.execute(
                    text(
                        "SELECT nodo_ref, capa, nombre, descripcion, modulo, texto_hash "
                        "FROM vec.definition_embeddings WHERE tenant_id = :t ORDER BY nodo_ref"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "nodos": [dict(n) for n in nodos],
        "aristas": [dict(a) for a in aristas],
        "embeddings": [dict(e) for e in embeddings],
    }


async def test_batch_grafo_es_convergente_e_idempotente(
    client_interna: AsyncClient,
    seed_dos_tenants: tuple[str, str],
) -> None:
    tenant_a, _ = seed_dos_tenants
    snap_id = f"snap_{uuid.uuid4().hex[:12]}"
    file_id = f"file_{uuid.uuid4().hex[:12]}"
    sym_id = f"sym_{uuid.uuid4().hex[:12]}"
    tbl_id = f"tbl_{uuid.uuid4().hex[:12]}"
    col_id = f"col_{uuid.uuid4().hex[:12]}"
    def_id = f"def_{uuid.uuid4().hex[:12]}"
    con_id = f"con_{uuid.uuid4().hex[:12]}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO cm.repo_snapshots
                   (id, tenant_id, commit_sha, rama, estado, fase_actual, stats)
                   VALUES (:sid, :t, '1234567890abcdef', 'main', 'en_progreso', 3,
                           '{"checkpoint_fase": 3}'::jsonb)"""
            ),
            {"sid": snap_id, "t": tenant_a},
        )
        await db.commit()

    lote = {
        "snapshot_id": snap_id,
        "nodos": [
            {
                "kind": "file",
                "id": file_id,
                "path": "src/pagos/cobros.py",
                "language": "python",
                "sha256": "a" * 64,
            },
            {
                "kind": "symbol",
                "id": sym_id,
                "file_id": file_id,
                "symbol_kind": "function",
                "name": "procesar_cobro",
                "start_line": 10,
                "end_line": 42,
                "signature": "def procesar_cobro(monto: int) -> bool",
                "descripcion": "Ejecuta el cobro idempotente.",
                "role": "domain",
            },
            {
                "kind": "db_table",
                "id": tbl_id,
                "esquema": "public",
                "nombre": "pagos",
            },
            {
                "kind": "db_column",
                "id": col_id,
                "table_id": tbl_id,
                "nombre": "monto_centavos",
                "tipo": "bigint",
                "nullable": False,
            },
            {
                "kind": "definition",
                "id": def_id,
                "modulo": "pagos",
                "nombre": "CobroIdempotente",
                "definition_kind": "definition",
                "descripcion": "Regla de cobro único por transacción.",
            },
            {
                "kind": "concept",
                "id": con_id,
                "name_en": "IdempotentCharge",
                "name_local": "Cobro Idempotente",
                "descripcion": "Garantía de no doble cargo.",
                "confianza": 0.92,
            },
        ],
        "aristas": [
            {
                "tipo": "DEFINES",
                "src_kind": "file",
                "src_id": file_id,
                "dst_kind": "symbol",
                "dst_id": sym_id,
                "props": {"confidence": 1.0},
            },
            {
                "tipo": "USES_TABLE",
                "src_kind": "symbol",
                "src_id": sym_id,
                "dst_kind": "db_table",
                "dst_id": tbl_id,
                "props": {"confidence": 0.95},
            },
        ],
        "embeddings": [
            {
                "nodo_ref": sym_id,
                "capa": "symbol",
                "nombre": "procesar_cobro",
                "descripcion": "Ejecuta el cobro idempotente.",
                "modulo": "pagos",
                "embedding": _vector_unitario(0.05),
                "texto_hash": "hash_sym_v1",
            }
        ],
    }

    r1 = await client_interna.post(
        "/internal/v1/codemapping/grafo/batch",
        json=lote,
        headers=_cabeceras_ingesta(tenant_a, f"ik-batch-1-{uuid.uuid4().hex[:8]}"),
    )
    assert r1.status_code == 200, r1.text
    cuerpo1 = r1.json()
    assert cuerpo1["nodos_procesados"] == 6
    assert cuerpo1["aristas_procesadas"] == 2
    assert cuerpo1["embeddings_procesados"] == 1
    assert cuerpo1["embeddings_actualizados"] == 1
    assert cuerpo1["limites_lote"]["max_nodos"] == 500

    estado_despues_1 = await _snapshot_grafo_db(tenant_a)
    assert len(estado_despues_1["nodos"]) == 6
    assert len(estado_despues_1["aristas"]) == 2
    assert len(estado_despues_1["embeddings"]) == 1

    # Segunda escritura con distinta Idempotency-Key: converge al mismo estado exacto
    r2 = await client_interna.post(
        "/internal/v1/codemapping/grafo/batch",
        json=lote,
        headers=_cabeceras_ingesta(tenant_a, f"ik-batch-2-{uuid.uuid4().hex[:8]}"),
    )
    assert r2.status_code == 200, r2.text
    cuerpo2 = r2.json()
    assert cuerpo2["embeddings_actualizados"] == 0

    estado_despues_2 = await _snapshot_grafo_db(tenant_a)
    assert estado_despues_2 == estado_despues_1


async def test_batch_grafo_rechaza_nodo_de_otro_tenant_con_403(
    client_interna: AsyncClient,
    seed_dos_tenants: tuple[str, str],
) -> None:
    tenant_a, tenant_b = seed_dos_tenants
    file_b_id = f"file_b_{uuid.uuid4().hex[:12]}"

    # 1) Intento con tenant_id explícito de otro tenant en un nodo -> 403
    r_explicit = await client_interna.post(
        "/internal/v1/codemapping/grafo/batch",
        json={
            "nodos": [
                {
                    "kind": "file",
                    "id": f"file_x_{uuid.uuid4().hex[:8]}",
                    "tenant_id": tenant_b,
                    "path": "src/secreto.py",
                    "language": "python",
                    "sha256": "b" * 64,
                }
            ]
        },
        headers=_cabeceras_ingesta(tenant_a, f"ik-cross-exp-{uuid.uuid4().hex[:8]}"),
    )
    assert r_explicit.status_code == 403, r_explicit.text

    # 2) Tenant B crea su propio archivo y luego Tenant A intenta referenciar/sobrescribir ese ID
    snap_b = f"snap_b_{uuid.uuid4().hex[:10]}"
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO cm.repo_snapshots
                   (id, tenant_id, commit_sha, rama, estado, fase_actual, stats)
                   VALUES (:sid, :t, 'fedcba9876543210', 'main', 'en_progreso', 1, '{}'::jsonb)"""
            ),
            {"sid": snap_b, "t": tenant_b},
        )
        await db.commit()

    r_seed_b = await client_interna.post(
        "/internal/v1/codemapping/grafo/batch",
        json={
            "nodos": [
                {
                    "kind": "file",
                    "id": file_b_id,
                    "snapshot_id": snap_b,
                    "path": "src/b/privado.py",
                    "language": "python",
                    "sha256": "c" * 64,
                }
            ]
        },
        headers=_cabeceras_ingesta(tenant_b, f"ik-seed-b-{uuid.uuid4().hex[:8]}"),
    )
    assert r_seed_b.status_code == 200, r_seed_b.text

    r_collision = await client_interna.post(
        "/internal/v1/codemapping/grafo/batch",
        json={
            "nodos": [
                {
                    "kind": "file",
                    "id": file_b_id,
                    "path": "src/a/robado.py",
                    "language": "python",
                    "sha256": "d" * 64,
                }
            ]
        },
        headers=_cabeceras_ingesta(tenant_a, f"ik-cross-id-{uuid.uuid4().hex[:8]}"),
    )
    assert r_collision.status_code == 403, r_collision.text

    # Verificar que el nodo de Tenant B sigue intacto y Tenant A no escribió nada
    assert len((await _snapshot_grafo_db(tenant_a))["nodos"]) == 0
    nodos_b = (await _snapshot_grafo_db(tenant_b))["nodos"]
    assert len(nodos_b) == 1
    assert nodos_b[0]["id"] == file_b_id


async def test_ingesta_status_y_apertura_escalacion_idempotente(
    client_interna: AsyncClient,
    seed_dos_tenants: tuple[str, str],
) -> None:
    tenant_a, tenant_b = seed_dos_tenants
    snap_id = f"snap_{uuid.uuid4().hex[:12]}"
    commit_sha = "abcdef1234567890"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO cm.repo_snapshots
                   (id, tenant_id, commit_sha, rama, estado, fase_actual, stats)
                   VALUES (:sid, :t, :sha, 'main', 'en_progreso', 4,
                           '{"checkpoint_fase": 4}'::jsonb)"""
            ),
            {"sid": snap_id, "t": tenant_a, "sha": commit_sha},
        )
        await db.execute(
            text(
                """INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload, estado)
                   VALUES (:qid, :t, 'enlace', '{"tipo":"CALLS"}'::jsonb, 'pendiente')"""
            ),
            {"qid": f"cq_{uuid.uuid4().hex[:12]}", "t": tenant_a},
        )
        await db.commit()

    # 1) Consultar estado de ingesta del tenant
    r_status = await client_interna.get(
        "/internal/v1/codemapping/ingesta/status",
        headers=_cabeceras_ingesta(tenant_a, f"ik-status-{uuid.uuid4().hex[:8]}"),
    )
    assert r_status.status_code == 200, r_status.text
    st = r_status.json()
    assert st["tenant_id"] == tenant_a
    assert st["snapshot_id"] == snap_id
    assert st["fase"]["numero"] == 4
    assert st["progreso"]["porcentaje"] == 80
    assert st["curacion_pendiente"]["total"] == 1
    assert st["curacion_pendiente"]["por_tipo"]["enlace"] == 1

    # 2) Abrir escalación de ingesta (201) y reintentar con misma Idempotency-Key (200)
    payload_esc = {
        "tenant_id": tenant_a,
        "repo_id": "repo_123",
        "commit_sha": commit_sha,
        "snapshot_id": snap_id,
        "trace_id": "tr_ingesta_001",
        "motivo": "tiers_agotados",
        "severidad": "media",
        "paso": "codemapping.describe_symbols",
        "tier": "fable",
        "lote": 2,
        "entrada_ref": f"repo_123@{commit_sha}",
        "diagnostico": {
            "tipo_error": "EscaladoSemantico",
            "mensaje": "Ningún tier produjo salida válida en lote 2",
            "intentos": 3,
            "frames": [
                {
                    "archivo": "packages/codemapping/src/kallicode_codemapping/nuggets.py",
                    "linea": 145,
                    "funcion": "describir_lote",
                }
            ],
            "tiers_probados": ["cheap", "balanced", "fable"],
        },
    }

    fake_redis = AsyncMock()
    ik_esc = f"cm-esc:{tenant_a}:repo_123:{commit_sha}:tiers_agotados"

    with patch("kallicode_api.routers.internal.redis_cliente", return_value=fake_redis):
        r_esc1 = await client_interna.post(
            "/internal/v1/codemapping/escalations",
            json=payload_esc,
            headers=_cabeceras_ingesta(tenant_a, ik_esc),
        )
        assert r_esc1.status_code == 201, r_esc1.text
        esc1 = r_esc1.json()
        assert esc1["escalation_id"].startswith("esc_")
        assert esc1["estado"] == "abierta"

        r_esc2 = await client_interna.post(
            "/internal/v1/codemapping/escalations",
            json=payload_esc,
            headers=_cabeceras_ingesta(tenant_a, ik_esc),
        )
        assert r_esc2.status_code == 200, r_esc2.text
        assert r_esc2.json() == esc1

        # Token de tenant_b intentando abrir escalación para tenant_a -> 403
        r_esc_forb = await client_interna.post(
            "/internal/v1/codemapping/escalations",
            json=payload_esc,
            headers=_cabeceras_ingesta(tenant_b, f"ik-forb-{uuid.uuid4().hex[:8]}"),
        )
        assert r_esc_forb.status_code == 403, r_esc_forb.text
