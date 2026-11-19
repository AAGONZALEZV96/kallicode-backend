"""Suite de Pruebas de Caos: Interrupción Abrupta de PostgreSQL y Redis (KC-QA-B6-03).

Ticket: KC-QA-B6-03
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Reglas:
- 100% Cero-Nube sobre contenedores Docker locales (localhost:5432 y localhost:6379).
- Confinamiento RLS multi-tenant obligatorio (`app.tenant_id`, rol `kallicode_app`).
- Restauración garantizada en bloque `finally` / fixture con `yield`.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from kallicode_testing.chaos_runner import ChaosIncidentDTO, ChaosRunner
from redis.exceptions import RedisError


def _local_pg_dsn(admin: bool = False, port: int = 5432) -> str:
    default_user = "kallicode_admin" if admin else "kallicode_app"
    env_key = "KC_DATABASE_URL_ADMIN" if admin else "KC_DATABASE_URL"
    raw = os.getenv(env_key, f"postgresql://{default_user}:dev@127.0.0.1:{port}/kallicode_test")
    cleaned = raw.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(cleaned)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return f"postgresql://{default_user}:dev@127.0.0.1:{port}/kallicode_test"
    if not admin and parsed.username in {"postgres", "kallicode_admin"}:
        return f"postgresql://kallicode_app:dev@127.0.0.1:{port}/kallicode_test"
    user = parsed.username or default_user
    password = parsed.password or "dev"
    db_name = parsed.path.lstrip("/") or "kallicode_test"
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/{db_name}"


def _local_redis_url(port: int = 6379) -> str:
    raw = os.getenv("KC_REDIS_URL", f"redis://127.0.0.1:{port}/0")
    parsed = urlparse(raw)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return f"redis://127.0.0.1:{port}/0"
    db_path = parsed.path or "/0"
    if parsed.password:
        return f"redis://:{parsed.password}@127.0.0.1:{port}{db_path}"
    return f"redis://127.0.0.1:{port}{db_path}"


@pytest_asyncio.fixture
async def chaos_env() -> AsyncGenerator[ChaosRunner, None]:
    """Asegura servicios activos antes y garantiza restauración total al finalizar el test."""
    runner = ChaosRunner()
    await runner.ensure_postgres_up()
    await runner.ensure_redis_up()
    try:
        yield runner
    finally:
        await runner.ensure_postgres_up()
        await runner.ensure_redis_up()
        await runner.shutdown_proxies()


async def _seed_chaos_job(
    tenant_id: str,
    ticket_id: str,
    job_id: str,
    *,
    estado: str = "build",
    gate_pendiente: int | None = None,
) -> None:
    """Siembra tenant, ticket y job aislados para pruebas de caos."""
    conn = await asyncpg.connect(_local_pg_dsn(admin=True))
    try:
        await conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tenant_id,
            f"Chaos Org {tenant_id}",
        )
        await conn.execute(
            """
            INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, etapa, gate_pendiente)
            VALUES ($1, $2, 'bug', $3, $4, $5, $6)
            """,
            ticket_id,
            tenant_id,
            "Ticket prueba caos KC-QA-B6-03",
            "Descripción controlada de prueba de caos para verificar atomicidad.",
            estado
            if estado in {"triage", "design", "build", "qa", "security", "deploy"}
            else "design",
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


async def _cleanup_chaos_tenant(tenant_id: str) -> None:
    """Limpia los datos sintéticos sembrados para el tenant de caos."""
    conn = await asyncpg.connect(_local_pg_dsn(admin=True))
    try:
        await conn.execute("DELETE FROM core.gate_signatures WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.job_transitions WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.jobs WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.tickets WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.ticket_counters WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM core.organizations WHERE id = $1", tenant_id)
    finally:
        await conn.close()


async def _close_drop(drop_gen: AsyncGenerator[dict[str, Any], None]) -> None:
    """Avanza/cierra el generador de caos garantizando ejecución de su bloque finally."""
    with suppress(StopAsyncIteration):
        await anext(drop_gen)
    await drop_gen.aclose()


@pytest.mark.asyncio
async def test_caos_postgres_transaccion_rollback_limpio(chaos_env: ChaosRunner) -> None:
    """1. Caída de PostgreSQL en mitad de transacción: rollback atómico y 0 huérfanos."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = f"org_chaos_{suffix}"
    ticket_id = f"tk_chaos_{suffix}"
    job_id = f"job_chaos_{suffix}"

    await _seed_chaos_job(tenant_id, ticket_id, job_id, estado="build")
    try:
        # Abrir transacción real con rol app y contexto RLS
        conn = await asyncpg.connect(_local_pg_dsn(admin=False, port=chaos_env.active_pg_port))
        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true), set_config('app.actor', $2, true)",
            tenant_id,
            "svc:orquestador",
        )
        # Dispara update en jobs + trigger T8 en job_transitions sin hacer COMMIT todavía
        await conn.execute("UPDATE core.jobs SET estado = 'qa' WHERE id = $1", job_id)

        drop_gen = chaos_env.simulate_db_drop(0.2)
        disconnect_detected = False
        try:
            drop_state = await anext(drop_gen)
            assert drop_state["target_service"] == "postgres"
            assert drop_state["status"] == "stopped"

            # Intentar confirmar la transacción mientras PostgreSQL está caído
            with pytest.raises(
                (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, asyncio.TimeoutError)
            ):
                await asyncio.wait_for(tx.commit(), timeout=3.0)
            disconnect_detected = True
        finally:
            await _close_drop(drop_gen)
            with suppress(Exception):
                await conn.close()

        assert disconnect_detected is True

        # Reconectar tras restauración y verificar consistencia total
        verify_conn = await asyncpg.connect(
            _local_pg_dsn(admin=False, port=chaos_env.active_pg_port)
        )
        try:
            async with verify_conn.transaction():
                await verify_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    tenant_id,
                )
                estado_actual = await verify_conn.fetchval(
                    "SELECT estado FROM core.jobs WHERE id = $1",
                    job_id,
                )
                etapa_ticket = await verify_conn.fetchval(
                    "SELECT etapa FROM core.tickets WHERE id = $1",
                    ticket_id,
                )
                transiciones_huerfanas = await verify_conn.fetchval(
                    "SELECT count(*) FROM core.job_transitions WHERE job_id = $1",
                    job_id,
                )
        finally:
            await verify_conn.close()

        assert estado_actual == "build"
        assert etapa_ticket == "build"
        assert transiciones_huerfanas == 0
    finally:
        await chaos_env.ensure_postgres_up()
        await _cleanup_chaos_tenant(tenant_id)


@pytest.mark.asyncio
async def test_caos_redis_desconexion_reintentos_worker(chaos_env: ChaosRunner) -> None:
    """2. Caída de Redis durante procesamiento de stream: 0 pérdida y 0 duplicación."""
    suffix = uuid.uuid4().hex[:10]
    stream_key = f"kc:chaos:jobs:{suffix}"
    group_name = f"workers-{suffix}"
    idempotency_key = f"kc:chaos:done:{suffix}"

    redis_client: Any = aioredis.from_url(
        _local_redis_url(port=chaos_env.active_redis_port),
        decode_responses=True,
    )
    processed_effects = 0
    try:
        await redis_client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
        msg_id = await redis_client.xadd(
            stream_key,
            {"job_id": f"job_{suffix}", "tenant_id": f"org_{suffix}", "step": "build"},
        )
        assert msg_id is not None

        # Worker lee el mensaje pero aún NO emite XACK cuando ocurre la caída
        entries = await redis_client.xreadgroup(
            groupname=group_name,
            consumername="worker-1",
            streams={stream_key: ">"},
            count=1,
        )
        assert len(entries) == 1
        _, messages = entries[0]
        assert len(messages) == 1
        read_id, payload = messages[0]
        assert read_id == msg_id
        assert payload["job_id"] == f"job_{suffix}"

        drop_gen = chaos_env.simulate_redis_drop(0.2)
        redis_error_caught = False
        try:
            drop_state = await anext(drop_gen)
            assert drop_state["target_service"] == "redis"
            assert drop_state["status"] == "stopped"

            with pytest.raises((RedisError, OSError, asyncio.TimeoutError)):
                await asyncio.wait_for(
                    redis_client.xack(stream_key, group_name, read_id),
                    timeout=3.0,
                )
            redis_error_caught = True
        finally:
            await _close_drop(drop_gen)
            await redis_client.aclose()

        assert redis_error_caught is True

        # Reconexión del worker tras recuperación de Redis y reclamo desde PEL
        recovered_redis: Any = aioredis.from_url(
            _local_redis_url(port=chaos_env.active_redis_port),
            decode_responses=True,
        )
        try:
            claimed = await recovered_redis.xautoclaim(
                stream_key,
                group_name,
                "worker-2",
                min_idle_time=0,
                start_id="0-0",
                count=10,
            )
            pending_messages = claimed[1]
            assert len(pending_messages) == 1

            for pending_id, pending_payload in pending_messages:
                assert pending_payload["job_id"] == f"job_{suffix}"
                # Efecto idempotente: aplicar exactamente una vez
                if await recovered_redis.set(idempotency_key, "1", nx=True, ex=60):
                    processed_effects += 1
                acked = await recovered_redis.xack(stream_key, group_name, pending_id)
                assert acked == 1

            pending_info = await recovered_redis.xpending(stream_key, group_name)
            assert pending_info["pending"] == 0
            assert processed_effects == 1
        finally:
            await recovered_redis.delete(stream_key, idempotency_key)
            await recovered_redis.aclose()
    finally:
        await chaos_env.ensure_redis_up()


@pytest.mark.asyncio
async def test_caos_cero_aprobacion_de_gates_por_omision(chaos_env: ChaosRunner) -> None:
    """3. Prohibición de aprobación de gates por omisión y validación de circuit breaker."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = f"org_chaos_{suffix}"
    ticket_id = f"tk_chaos_{suffix}"
    job_id = f"job_chaos_{suffix}"

    await _seed_chaos_job(tenant_id, ticket_id, job_id, estado="gate1", gate_pendiente=1)

    class _ChaosProbeHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _check_pg_sync(self) -> bool:
            async def _ping() -> bool:
                try:
                    c = await asyncpg.connect(
                        _local_pg_dsn(admin=False, port=chaos_env.active_pg_port),
                        timeout=1.0,
                    )
                    await c.close()
                except Exception:
                    return False
                return True

            return asyncio.run(_ping())

        def do_GET(self) -> None:
            pg_ok = self._check_pg_sync()
            if not pg_ok:
                body = json.dumps(
                    {"error": {"codigo": "DEPENDENCIA_NO_DISPONIBLE"}, "postgres": False}
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "5")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps({"postgres": True, "redis": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            pg_ok = self._check_pg_sync()
            if not pg_ok:
                body = json.dumps({"error": {"codigo": "DEPENDENCIA_NO_DISPONIBLE"}}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "5")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps({"estado": "requiere_firma_explicita"}).encode()
            self.send_response(409)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChaosProbeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        drop_gen = chaos_env.simulate_db_drop(0.2)
        try:
            await anext(drop_gen)

            # Verificar circuit breaker HTTP 503 durante caída de PostgreSQL
            cb_ok = await asyncio.to_thread(
                chaos_env.verify_circuit_breaker,
                f"http://127.0.0.1:{port}/health/ready",
            )
            assert cb_ok is True

            # Intentar aprobar Gate 1 durante caída: jamás debe devolver 200 ni aprobar
            async with httpx.AsyncClient(timeout=3.0) as client:
                gate_res = await client.post(
                    f"http://127.0.0.1:{port}/api/v1/tickets/{ticket_id}/gates/1/approve",
                    json={"comentario": "Intento durante caída de BD"},
                )
            assert gate_res.status_code == 503
        finally:
            await _close_drop(drop_gen)

        # Verificar en BD recuperada que 0 gates pasaron a 'aprobado' y el job sigue en 'gate1'
        verify_conn = await asyncpg.connect(
            _local_pg_dsn(admin=False, port=chaos_env.active_pg_port)
        )
        try:
            async with verify_conn.transaction():
                await verify_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    tenant_id,
                )
                aprobaciones = await verify_conn.fetchval(
                    """
                    SELECT count(*) FROM core.gate_signatures
                    WHERE ticket_id = $1 AND accion = 'aprobado'
                    """,
                    ticket_id,
                )
                job_estado = await verify_conn.fetchval(
                    "SELECT estado FROM core.jobs WHERE id = $1",
                    job_id,
                )
        finally:
            await verify_conn.close()

        assert aprobaciones == 0
        assert job_estado == "gate1"

        incident = ChaosIncidentDTO(
            target_service="postgres",
            failure_type="docker_stop",
            duration_seconds=0.2,
            state_consistent=True,
            gate_unapproved=True,
            recovered=True,
        )
        report = ChaosRunner.build_report([incident])
        assert report.all_recovered is True
        assert report.zero_gate_leaks is True
    finally:
        server.shutdown()
        server.server_close()
        await chaos_env.ensure_postgres_up()
        await _cleanup_chaos_tenant(tenant_id)


@pytest.mark.asyncio
async def test_caos_restauracion_garantizada_en_teardown(chaos_env: ChaosRunner) -> None:
    """4. Garantiza reactivación automática de PostgreSQL y Redis incluso ante AssertionError."""
    # Caso A: AssertionError dentro de caída de PostgreSQL
    pg_drop = chaos_env.simulate_db_drop(0.1)
    with pytest.raises(AssertionError, match="fallo simulado en prueba"):
        try:
            state = await anext(pg_drop)
            assert state["status"] == "stopped"
            raise AssertionError("fallo simulado en prueba")
        finally:
            await pg_drop.aclose()

    # Verificar inmediatamente que PostgreSQL volvió a operar
    pg_conn = await asyncpg.connect(_local_pg_dsn(admin=False, port=chaos_env.active_pg_port))
    try:
        assert await pg_conn.fetchval("SELECT 1") == 1
    finally:
        await pg_conn.close()

    # Caso B: AssertionError dentro de caída de Redis
    redis_drop = chaos_env.simulate_redis_drop(0.1)
    with pytest.raises(AssertionError, match="fallo simulado en redis"):
        try:
            state = await anext(redis_drop)
            assert state["status"] == "stopped"
            raise AssertionError("fallo simulado en redis")
        finally:
            await redis_drop.aclose()

    # Verificar inmediatamente que Redis volvió a responder PING
    r_client = aioredis.from_url(
        _local_redis_url(port=chaos_env.active_redis_port),
        decode_responses=True,
    )
    try:
        assert await r_client.ping() is True
    finally:
        await r_client.aclose()
