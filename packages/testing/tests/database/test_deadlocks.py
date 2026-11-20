"""Suite de Pruebas de Deadlocks (40P01) y Concurrencia SQL en PostgreSQL 16 (KC-QA-B6-04).

Ticket: KC-QA-B6-04
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Reglas:
- 100% Cero-Nube contra PostgreSQL 16 real local (`localhost:5432/kallicode_test`).
- Uso exclusivo del rol no-superusuario `kallicode_app` con aislamiento RLS (`app.tenant_id`).
- Limpieza garantizada post-ejecución (ON DELETE CASCADE desde `core.tickets`/`core.organizations`).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from kallicode_core.errors import AppError
from kallicode_testing.deadlock_simulator import (
    DeadlockSimulator,
    UnhandledDeadlockError,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _app_asyncpg_sqlalchemy_url() -> str:
    """Normaliza la URL local forzando el rol no-superusuario `kallicode_app`."""
    raw = os.getenv(
        "KC_DATABASE_URL",
        "postgresql+asyncpg://kallicode_app:dev@127.0.0.1:5432/kallicode_test",
    )
    parsed = urlparse(raw.replace("postgresql://", "postgresql+asyncpg://"))
    if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.username in {
        "postgres",
        "kallicode_admin",
    }:
        return "postgresql+asyncpg://kallicode_app:dev@127.0.0.1:5432/kallicode_test"
    db_name = parsed.path.lstrip("/") or "kallicode_test"
    password = parsed.password or "dev"
    port = parsed.port or 5432
    return f"postgresql+asyncpg://{parsed.username}:{password}@{parsed.hostname}:{port}/{db_name}"


def _admin_asyncpg_url() -> str:
    raw = os.getenv(
        "KC_DATABASE_URL_ADMIN",
        "postgresql://kallicode_admin:dev@127.0.0.1:5432/kallicode_test",
    )
    cleaned = raw.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(cleaned)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return "postgresql://kallicode_admin:dev@127.0.0.1:5432/kallicode_test"
    return cleaned


@pytest_asyncio.fixture
async def app_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Provee un sessionmaker asíncrono con pool independiente por corrutina bajo `kallicode_app`."""
    engine = create_async_engine(
        _app_asyncpg_sqlalchemy_url(),
        pool_size=12,
        max_overflow=10,
        echo=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_tenant_and_jobs(
    tenant_id: str,
    user_id: str,
    jobs_spec: list[tuple[str, str, str, int | None]],
) -> None:
    """Siembra tenant, usuario aprobador y pares (ticket_id, job_id, estado, gate_pendiente)."""
    conn = await asyncpg.connect(_admin_asyncpg_url())
    try:
        await conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tenant_id,
            f"Deadlock Org {tenant_id}",
        )
        await conn.execute(
            """
            INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
            VALUES ($1, $2, $3, 'Approver QA2', 'approver', 'activo')
            ON CONFLICT DO NOTHING
            """,
            user_id,
            tenant_id,
            f"{user_id}@kallicode.local",
        )
        for ticket_id, job_id, estado, gate_pendiente in jobs_spec:
            etapa = "design" if estado == "gate1" else estado
            if etapa == "pendiente_asignacion":
                etapa = "triage"
            await conn.execute(
                """
                INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, etapa, gate_pendiente)
                VALUES ($1, $2, 'bug', $3, $4, $5, $6)
                """,
                ticket_id,
                tenant_id,
                f"Deadlock Test {ticket_id}",
                "Prueba de concurrencia SQL y deadlocks KC-QA-B6-04.",
                etapa,
                gate_pendiente,
            )
            await conn.execute(
                """
                INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                VALUES ($1, $2, $3, $4, 'P2')
                """,
                job_id,
                tenant_id,
                ticket_id,
                estado,
            )
    finally:
        await conn.close()


async def _cleanup_tenant(tenant_id: str) -> None:
    """Limpia los datos del tenant aprovechando ON DELETE CASCADE desde tickets/jobs."""
    conn = await asyncpg.connect(_admin_asyncpg_url())
    try:
        await conn.execute("DELETE FROM core.jobs WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.tickets WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.ticket_counters WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.users WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.organizations WHERE id = $1", tenant_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_deadlock_cruzado_detectado_y_recuperado(
    app_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """1. Provoca 40P01 real cruzado en `core.jobs`, ejecuta retry y logra consistencia final."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = f"org_dl_{suffix}"
    user_id = f"usr_dl_{suffix}"
    t1, j1 = f"tk_dl1_{suffix}", f"job_dl1_{suffix}"
    t2, j2 = f"tk_dl2_{suffix}", f"job_dl2_{suffix}"

    await _seed_tenant_and_jobs(
        tenant_id,
        user_id,
        [(t1, j1, "build", None), (t2, j2, "build", None)],
    )
    try:
        barrier = asyncio.Barrier(2)
        a_attempts = 0
        b_attempts = 0

        @DeadlockSimulator.retry_on_deadlock(max_retries=4, initial_delay_ms=25.0)
        async def tx_a(
            factory: async_sessionmaker[AsyncSession],
            bar: asyncio.Barrier,
        ) -> None:
            nonlocal a_attempts
            a_attempts += 1
            async with factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": tenant_id},
                )
                # Bloquea J1 primero
                await session.execute(
                    text("UPDATE core.jobs SET prioridad = 'P1' WHERE id = :j"),
                    {"j": j1},
                )
                if a_attempts == 1:
                    await bar.wait()
                # Intenta bloquear J2 mientras Tx B lo retiene -> provoca 40P01
                await session.execute(
                    text("UPDATE core.jobs SET prioridad = 'P3' WHERE id = :j"),
                    {"j": j2},
                )

        @DeadlockSimulator.retry_on_deadlock(max_retries=4, initial_delay_ms=25.0)
        async def tx_b(
            factory: async_sessionmaker[AsyncSession],
            bar: asyncio.Barrier,
        ) -> None:
            nonlocal b_attempts
            b_attempts += 1
            async with factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": tenant_id},
                )
                # Bloquea J2 primero
                await session.execute(
                    text("UPDATE core.jobs SET contexto = CAST(:c AS jsonb) WHERE id = :j"),
                    {"c": '{"tx":"b1"}', "j": j2},
                )
                if b_attempts == 1:
                    await bar.wait()
                # Intenta bloquear J1 mientras Tx A lo retiene -> provoca 40P01
                await session.execute(
                    text("UPDATE core.jobs SET contexto = CAST(:c AS jsonb) WHERE id = :j"),
                    {"c": '{"tx":"b2"}', "j": j1},
                )

        dto_a, dto_b = await DeadlockSimulator.execute_concurrent_updates(
            app_session_factory,
            tx_a,
            tx_b,
            barrier,
        )

        assert dto_a.succeeded is True, f"Tx A falló: {dto_a.error_message}"
        assert dto_b.succeeded is True, f"Tx B falló: {dto_b.error_message}"
        assert dto_a.deadlock_detected or dto_b.deadlock_detected, (
            "Se esperaba detectar al menos un SQLSTATE 40P01 real entre las transacciones cruzadas."
        )
        assert max(dto_a.attempts_count, dto_b.attempts_count) >= 2

        # Verificar persistencia final consistente en PostgreSQL bajo RLS
        async with app_session_factory() as verify_session, verify_session.begin():
            await verify_session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": tenant_id},
            )
            res_j1 = (
                await verify_session.execute(
                    text("SELECT prioridad, contexto FROM core.jobs WHERE id = :j"),
                    {"j": j1},
                )
            ).one()
            res_j2 = (
                await verify_session.execute(
                    text("SELECT prioridad, contexto FROM core.jobs WHERE id = :j"),
                    {"j": j2},
                )
            ).one()

        assert res_j1.prioridad == "P1"
        assert res_j1.contexto == {"tx": "b2"}
        assert res_j2.prioridad == "P3"
        assert res_j2.contexto == {"tx": "b1"}

        report = DeadlockSimulator.build_report([dto_a, dto_b], total_scenarios=1)
        assert report.deadlocks_provoked >= 1
        assert report.retries_succeeded >= 1
        assert report.is_conforming is True
        exported = DeadlockSimulator.export_report_json(
            report,
            tmp_path / "deadlock-test-report.json",
        )
        assert exported.exists()
    finally:
        await _cleanup_tenant(tenant_id)


@pytest.mark.asyncio
async def test_alta_concurrencia_transiciones_job(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """2. 10 corrutinas concurrentes transicionando el mismo job con consistencia íntegra."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = f"org_conc_{suffix}"
    user_id = f"usr_conc_{suffix}"
    t1, j1 = f"tk_conc_{suffix}", f"job_conc_{suffix}"

    ordered_states = (
        "pendiente_asignacion",
        "triage",
        "design",
        "gate1",
        "build",
        "qa",
        "gate2",
        "security",
        "deploy_preflight",
        "gate3",
        "deploy",
    )

    await _seed_tenant_and_jobs(
        tenant_id,
        user_id,
        [(t1, j1, "pendiente_asignacion", None)],
    )
    try:

        @DeadlockSimulator.retry_on_deadlock(max_retries=6, initial_delay_ms=15.0)
        async def advance_one_step(worker_idx: int) -> str:
            async with app_session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', :t, true), set_config('app.actor', :a, true)"
                    ),
                    {"t": tenant_id, "a": f"svc:worker_{worker_idx}"},
                )
                current_state = (
                    await session.execute(
                        text("SELECT estado FROM core.jobs WHERE id = :j FOR UPDATE"),
                        {"j": j1},
                    )
                ).scalar_one()
                idx = ordered_states.index(str(current_state))
                next_state = ordered_states[idx + 1]
                await session.execute(
                    text("UPDATE core.jobs SET estado = :e WHERE id = :j"),
                    {"e": next_state, "j": j1},
                )
                return next_state

        results = await asyncio.gather(*(advance_one_step(i) for i in range(10)))
        assert len(results) == 10

        async with app_session_factory() as verify_session, verify_session.begin():
            await verify_session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": tenant_id},
            )
            final_state = (
                await verify_session.execute(
                    text("SELECT estado FROM core.jobs WHERE id = :j"),
                    {"j": j1},
                )
            ).scalar_one()
            transitions = (
                await verify_session.execute(
                    text(
                        """
                        SELECT de_estado, a_estado
                          FROM core.job_transitions
                         WHERE job_id = :j
                         ORDER BY id ASC
                        """
                    ),
                    {"j": j1},
                )
            ).all()

        assert final_state == "deploy"
        assert len(transitions) == 10
        expected_pairs = [
            (ordered_states[i], ordered_states[i + 1]) for i in range(len(ordered_states) - 1)
        ]
        observed_pairs = [(row.de_estado, row.a_estado) for row in transitions]
        assert observed_pairs == expected_pairs
    finally:
        await _cleanup_tenant(tenant_id)


@pytest.mark.asyncio
async def test_cero_doble_aprobacion_gate_bajo_carrera(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """3. Carrera concurrente sobre Gate 1: máximo una firma aprobada y conflicto controlado."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = f"org_gate_{suffix}"
    user_id = f"usr_gate_{suffix}"
    t1, j1 = f"tk_gate_{suffix}", f"job_gate_{suffix}"

    await _seed_tenant_and_jobs(
        tenant_id,
        user_id,
        [(t1, j1, "gate1", 1)],
    )
    try:
        race_barrier = asyncio.Barrier(2)

        async def approve_gate_race(attempt_tag: str) -> tuple[str, int]:
            try:
                async with app_session_factory() as session, session.begin():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"),
                        {"t": tenant_id},
                    )
                    # Sincronizar ambas corrutinas justo antes de intentar firmar
                    await race_barrier.wait()

                    sello = hashlib.sha256(f"{t1}:1:{attempt_tag}".encode()).hexdigest()
                    await session.execute(
                        text(
                            """
                            INSERT INTO core.gate_signatures
                                (id, tenant_id, ticket_id, gate, accion, actor_id, comentario, sello)
                            VALUES (:id, :t, :tk, 1, 'aprobado', :u, :c, :sello)
                            """
                        ),
                        {
                            "id": f"gs_{uuid.uuid4().hex[:16]}",
                            "t": tenant_id,
                            "tk": t1,
                            "u": user_id,
                            "c": f"Aprobación concurrente {attempt_tag}",
                            "sello": sello,
                        },
                    )
                    await session.execute(
                        text("UPDATE core.jobs SET estado = 'build' WHERE id = :j"),
                        {"j": j1},
                    )
                    return ("approved", 200)
            except IntegrityError as exc:
                # Traducción controlada equivalente a gates.py -> HTTP 409 Conflict
                err = AppError(
                    "GATE_NO_ACTIVO",
                    409,
                    f"Gate ya firmado por petición concurrente ({type(exc).__name__}).",
                )
                return ("conflict", err.http)

        outcomes = await asyncio.gather(
            approve_gate_race("req_1"),
            approve_gate_race("req_2"),
        )
        statuses = sorted(status for _, status in outcomes)
        assert statuses == [200, 409]

        async with app_session_factory() as verify_session, verify_session.begin():
            await verify_session.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": tenant_id},
            )
            approved_count = (
                await verify_session.execute(
                    text(
                        """
                        SELECT count(*)
                          FROM core.gate_signatures
                         WHERE ticket_id = :tk AND gate = 1 AND accion = 'aprobado'
                        """
                    ),
                    {"tk": t1},
                )
            ).scalar_one()
            job_estado = (
                await verify_session.execute(
                    text("SELECT estado FROM core.jobs WHERE id = :j"),
                    {"j": j1},
                )
            ).scalar_one()

        assert approved_count == 1
        assert job_estado == "build"
    finally:
        await _cleanup_tenant(tenant_id)


@pytest.mark.asyncio
async def test_agotamiento_de_reintentos_falla_controlada(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """4. Interbloqueo persistente excede max_retries y se traduce a 409/503 sin 500 crudo."""
    attempts_observed = 0

    @DeadlockSimulator.retry_on_deadlock(max_retries=2, initial_delay_ms=10.0)
    async def persistent_deadlock_query() -> None:
        nonlocal attempts_observed
        attempts_observed += 1
        async with app_session_factory() as session, session.begin():
            # PostgreSQL 16 emite un SQLSTATE 40P01 auténtico en cada intento
            await session.execute(
                text(
                    "DO $$ BEGIN "
                    "RAISE EXCEPTION 'deadlock persistente simulado' USING ERRCODE = '40P01'; "
                    "END $$;"
                )
            )

    app = FastAPI()

    @app.exception_handler(UnhandledDeadlockError)
    async def _handle_deadlock(_request: Any, exc: UnhandledDeadlockError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(exc.retry_after_s)},
            content={"error": {"codigo": exc.codigo, "mensaje": str(exc)}},
        )

    @app.post("/api/v1/test/deadlock-endpoint")
    async def _endpoint() -> dict[str, bool]:
        await persistent_deadlock_query()
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/v1/test/deadlock-endpoint")

    assert attempts_observed == 3  # 1 intento inicial + 2 reintentos
    assert response.status_code in (409, 503)
    if response.status_code == 503:
        assert response.headers.get("Retry-After") == "1"
    body = response.json()
    assert body["error"]["codigo"] == "CONFLICTO_CONCURRENCIA"
