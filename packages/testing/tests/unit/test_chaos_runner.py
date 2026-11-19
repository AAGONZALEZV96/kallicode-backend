"""Pruebas unitarias para ChaosRunner y DTOs de caos (KC-QA-B6-03).

Ticket: KC-QA-B6-03
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from kallicode_testing.chaos_runner import (
    ChaosIncidentDTO,
    ChaosRecoveryError,
    ChaosRunner,
    ChaosTestReportDTO,
)
from pydantic import ValidationError


def test_inmutabilidad_dtos_caos() -> None:
    """Confirma que ChaosIncidentDTO y ChaosTestReportDTO tienen frozen=True."""
    incident = ChaosIncidentDTO(
        target_service="postgres",
        failure_type="docker_stop",
        duration_seconds=1.5,
        state_consistent=True,
        gate_unapproved=True,
        recovered=True,
    )
    report = ChaosTestReportDTO(
        incidents=[incident],
        all_recovered=True,
        zero_gate_leaks=True,
    )

    with pytest.raises(ValidationError):
        incident.recovered = False

    with pytest.raises(ValidationError):
        report.zero_gate_leaks = False


@pytest.mark.asyncio
async def test_simulate_db_drop_restauracion_garantizada_en_error() -> None:
    """Verifica que simulate_db_drop ejecute start y readiness incluso ante AssertionError."""
    runner = ChaosRunner(
        postgres_container="pg-test-unit",
        redis_container="redis-test-unit",
        recovery_timeout_seconds=1.0,
        mode="docker",
    )

    with (
        patch.object(
            runner,
            "_run_docker",
            new=AsyncMock(return_value=(0, "ok", "")),
        ) as mock_docker,
        patch.object(
            runner,
            "_check_postgres_ready",
            new=AsyncMock(return_value=True),
        ) as mock_ready,
    ):
        drop_gen = runner.simulate_db_drop(0.0)
        with pytest.raises(AssertionError, match="fallo simulado"):
            try:
                state = await anext(drop_gen)
                assert state["target_service"] == "postgres"
                assert state["status"] == "stopped"
                raise AssertionError("fallo simulado")
            finally:
                await drop_gen.aclose()

        docker_calls = [call.args for call in mock_docker.await_args_list]
        assert ("stop", "-t", "5", "pg-test-unit") in docker_calls
        assert ("start", "pg-test-unit") in docker_calls
        assert mock_ready.await_count >= 1


@pytest.mark.asyncio
async def test_simulate_redis_drop_exitoso_y_timeout_error() -> None:
    """Verifica restauración de Redis y lanzamiento de ChaosRecoveryError si no responde."""
    runner = ChaosRunner(
        postgres_container="pg-test-unit",
        redis_container="redis-test-unit",
        recovery_timeout_seconds=0.3,
        mode="docker",
    )

    # Caso exitoso: Redis vuelve con PING -> PONG
    with (
        patch.object(
            runner,
            "_run_docker",
            new=AsyncMock(return_value=(0, "ok", "")),
        ) as mock_docker,
        patch.object(
            runner,
            "_check_redis_ready",
            new=AsyncMock(return_value=True),
        ),
    ):
        drop_gen = runner.simulate_redis_drop(0.0)
        state = await anext(drop_gen)
        assert state["target_service"] == "redis"
        assert state["status"] == "stopped"
        with pytest.raises(StopAsyncIteration):
            await anext(drop_gen)

        docker_calls = [call.args for call in mock_docker.await_args_list]
        assert ("stop", "-t", "5", "redis-test-unit") in docker_calls
        assert ("start", "redis-test-unit") in docker_calls

    # Caso de fallo en recuperación: Redis no responde tras start
    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(0, "ok", ""))),
        patch.object(runner, "_check_redis_ready", new=AsyncMock(return_value=False)),
        pytest.raises(ChaosRecoveryError, match="no respondió PONG"),
    ):
        await runner.ensure_redis_up()

    # Caso de fallo en stop de Docker
    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", "not found"))),
        pytest.raises(ChaosRecoveryError, match="No se pudo detener"),
    ):
        bad_gen = runner.simulate_db_drop(0.0)
        await anext(bad_gen)


@respx.mock
def test_verify_circuit_breaker_estados() -> None:
    """Valida detección de degradación controlada (503/ready=False) vs errores 500 o falso 200."""
    # 1. HTTP 503 controlado -> True
    respx.get("http://api.local/tickets").mock(
        return_value=httpx.Response(503, json={"error": {"codigo": "DEPENDENCIA_NO_DISPONIBLE"}})
    )
    assert ChaosRunner.verify_circuit_breaker("http://api.local/tickets") is True

    # 2. HTTP 200 en /health/ready con postgres=False -> True
    respx.get("http://api.local/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": False, "redis": True})
    )
    assert ChaosRunner.verify_circuit_breaker("http://api.local/health/ready") is True

    # 3. HTTP 200 falsamente sano durante caída -> False
    respx.get("http://api.local/health/ready").mock(
        return_value=httpx.Response(200, json={"postgres": True, "redis": True})
    )
    assert ChaosRunner.verify_circuit_breaker("http://api.local/health/ready") is False

    # 4. HTTP 500 sin controlar -> False
    respx.get("http://api.local/broken").mock(return_value=httpx.Response(500, text="Crash"))
    assert ChaosRunner.verify_circuit_breaker("http://api.local/broken") is False

    # 5. Error de red -> False
    respx.get("http://api.local/down").mock(side_effect=httpx.ConnectError("Connection refused"))
    assert ChaosRunner.verify_circuit_breaker("http://api.local/down") is False


def test_build_report_y_export_json(tmp_path: Path) -> None:
    """Verifica consolidación anti falsos verdes y exportación WORM determinista."""
    empty_report = ChaosRunner.build_report([])
    assert empty_report.all_recovered is False
    assert empty_report.zero_gate_leaks is False

    incidents = [
        ChaosIncidentDTO(
            target_service="postgres",
            failure_type="docker_stop",
            duration_seconds=0.5,
            state_consistent=True,
            gate_unapproved=True,
            recovered=True,
        ),
        ChaosIncidentDTO(
            target_service="redis",
            failure_type="docker_stop",
            duration_seconds=0.5,
            state_consistent=True,
            gate_unapproved=True,
            recovered=True,
        ),
    ]
    report = ChaosRunner.build_report(incidents)
    assert report.all_recovered is True
    assert report.zero_gate_leaks is True

    exported = ChaosRunner.export_report_json(report, tmp_path / "worm" / "chaos-report.json")
    assert exported.exists()
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["report_id"] == report.report_id
    assert len(data["incidents"]) == 2
    assert data["zero_gate_leaks"] is True


@pytest.mark.asyncio
async def test_helpers_internos_docker_readiness_y_bordes() -> None:
    """Cubre _run_docker, _check_postgres_ready, _check_redis_ready y errores de arranque."""
    runner = ChaosRunner(recovery_timeout_seconds=0.2, mode="docker")

    # _run_docker con subprocess mockeado
    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
    with patch(
        "kallicode_testing.chaos_runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ):
        rc, out, err = await runner._run_docker("ps")
        assert rc == 0
        assert out == "ok"
        assert err == ""

    # _check_postgres_ready: pg_isready=1 -> False; pg_isready=0 + OSError -> False; éxito -> True
    with patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", ""))):
        assert await runner._check_postgres_ready() is False

    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(0, "", ""))),
        patch(
            "kallicode_testing.chaos_runner.asyncio.open_connection",
            new=AsyncMock(side_effect=OSError("refused")),
        ),
    ):
        assert await runner._check_postgres_ready() is False

    mock_writer = AsyncMock()
    mock_writer.close = lambda: None
    mock_writer.wait_closed = AsyncMock()
    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(0, "", ""))),
        patch(
            "kallicode_testing.chaos_runner.asyncio.open_connection",
            new=AsyncMock(return_value=(AsyncMock(), mock_writer)),
        ),
    ):
        assert await runner._check_postgres_ready() is True

    # _check_redis_ready: OSError -> False; PONG -> True
    with patch(
        "kallicode_testing.chaos_runner.asyncio.open_connection",
        new=AsyncMock(side_effect=OSError("refused")),
    ):
        assert await runner._check_redis_ready() is False

    mock_reader = AsyncMock()
    mock_reader.readline = AsyncMock(return_value=b"+PONG\r\n")
    mock_r_writer = AsyncMock()
    mock_r_writer.write = lambda _b: None
    mock_r_writer.drain = AsyncMock()
    mock_r_writer.close = lambda: None
    mock_r_writer.wait_closed = AsyncMock()
    with patch(
        "kallicode_testing.chaos_runner.asyncio.open_connection",
        new=AsyncMock(return_value=(mock_reader, mock_r_writer)),
    ):
        assert await runner._check_redis_ready() is True

    # ensure_postgres_up y ensure_redis_up: error de docker start y timeout
    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", "err"))),
        pytest.raises(ChaosRecoveryError, match="Fallo al iniciar contenedor PostgreSQL"),
    ):
        await runner.ensure_postgres_up()

    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(0, "", ""))),
        patch.object(runner, "_check_postgres_ready", new=AsyncMock(return_value=False)),
        pytest.raises(ChaosRecoveryError, match="no respondió ready"),
    ):
        await runner.ensure_postgres_up()

    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", "err"))),
        pytest.raises(ChaosRecoveryError, match="Fallo al iniciar contenedor Redis"),
    ):
        await runner.ensure_redis_up()

    with (
        patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", "not found"))),
        pytest.raises(ChaosRecoveryError, match="No se pudo detener contenedor Redis"),
    ):
        bad_redis = runner.simulate_redis_drop(0.0)
        await anext(bad_redis)


@respx.mock
def test_verify_circuit_breaker_json_invalido() -> None:
    """Retorna False ante respuesta 200 con cuerpo no JSON."""
    respx.get("http://api.local/non-json").mock(return_value=httpx.Response(200, text="<html>"))
    assert ChaosRunner.verify_circuit_breaker("http://api.local/non-json") is False


@pytest.mark.asyncio
async def test_tcp_proxy_fallback_sin_contenedor_docker() -> None:
    """Simula CI sin contenedor Docker nombrado: activa corte TCP y restaura en finally."""
    import asyncio

    async def _echo_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    upstream = await asyncio.start_server(_echo_handler, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])

    try:
        runner = ChaosRunner(
            postgres_container="inexistente-ci-pg",
            pg_port=upstream_port,
            recovery_timeout_seconds=2.0,
            mode="auto",
        )
        with patch.object(runner, "_run_docker", new=AsyncMock(return_value=(1, "", "no such"))):
            await runner.ensure_postgres_up()
            assert runner.active_pg_port != upstream_port

            # Conectar cliente a través del proxy TCP
            reader, writer = await asyncio.open_connection("127.0.0.1", runner.active_pg_port)
            writer.write(b"PING")
            await writer.drain()
            assert await reader.read(4) == b"PING"

            drop_gen = runner.simulate_db_drop(0.0)
            try:
                state = await anext(drop_gen)
                assert state["failure_type"] == "tcp_disconnect"
                assert state["status"] == "stopped"

                # Nuevas conexiones deben ser rechazadas mientras está caído
                with pytest.raises(OSError):
                    await asyncio.open_connection("127.0.0.1", runner.active_pg_port)
            finally:
                try:
                    await anext(drop_gen)
                except StopAsyncIteration:
                    pass
                await drop_gen.aclose()
                writer.close()

            # Tras el finally, el proxy vuelve a aceptar conexiones en el mismo puerto
            r2, w2 = await asyncio.open_connection("127.0.0.1", runner.active_pg_port)
            w2.write(b"OK")
            await w2.drain()
            assert await r2.read(2) == b"OK"
            w2.close()
            await w2.wait_closed()
            await runner.shutdown_proxies()
    finally:
        upstream.close()
        try:
            await asyncio.wait_for(upstream.wait_closed(), timeout=1.0)
        except TimeoutError:
            pass
