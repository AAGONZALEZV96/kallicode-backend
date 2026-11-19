"""Orquestador de Pruebas de Caos e Inyección Controlada de Fallos (KC-QA-B6-03).

Ticket: KC-QA-B6-03
Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
Propósito: Simular interrupciones abruptas de PostgreSQL y Redis locales (vía `docker stop`
o desconexión controlada de socket TCP en entornos CI con service containers) con
restauración garantizada en bloque `finally`, validar circuit breakers (HTTP 503) y
exportar reportes Pydantic v2 inmutables (`frozen=True`) para sellado WORM.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()

DEFAULT_PG_CONTAINER = "kallicode-backend-postgres-1"
DEFAULT_REDIS_CONTAINER = "kallicode-backend-redis-1"


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class ChaosRecoveryError(RuntimeError):
    """Lanzada cuando un servicio detenido durante caos no logra restaurarse a tiempo."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B6-03 (Inmutables)
# ---------------------------------------------------------------------------
class ChaosIncidentDTO(BaseModel):
    """Registro inmutable de un incidente de caos ejecutado sobre una dependencia."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    target_service: str
    failure_type: str
    duration_seconds: float
    state_consistent: bool
    gate_unapproved: bool
    recovered: bool


class ChaosTestReportDTO(BaseModel):
    """Reporte consolidado e inmutable de la batería de pruebas de caos."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(default_factory=lambda: f"chaos-{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    incidents: list[ChaosIncidentDTO] = Field(default_factory=list)
    all_recovered: bool
    zero_gate_leaks: bool


# ---------------------------------------------------------------------------
# Proxy TCP local para corte controlado de sockets (Cero-Nube / CI services)
# ---------------------------------------------------------------------------
class _TcpFaultProxy:
    """Túnel TCP local que permite corte abrupto de sockets activos y restauración limpia."""

    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.listen_port: int = 0
        self._server: asyncio.Server | None = None
        self._active_writers: set[asyncio.StreamWriter] = set()
        self._pipe_tasks: set[asyncio.Task[None]] = set()

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            with suppress(Exception):
                writer.close()

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        self._active_writers.add(client_writer)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host,
                self.upstream_port,
            )
            self._active_writers.add(upstream_writer)
            t1 = asyncio.create_task(self._pipe(client_reader, upstream_writer))
            t2 = asyncio.create_task(self._pipe(upstream_reader, client_writer))
            self._pipe_tasks.update((t1, t2))
            await asyncio.gather(t1, t2, return_exceptions=True)
        except OSError:
            pass
        finally:
            self._active_writers.discard(client_writer)
            with suppress(Exception):
                client_writer.close()
            if upstream_writer is not None:
                self._active_writers.discard(upstream_writer)
                with suppress(Exception):
                    upstream_writer.close()

    async def start(self) -> int:
        """Inicia o reanuda la escucha TCP manteniendo el mismo puerto asignado."""
        if self._server is not None and self._server.is_serving():
            return self.listen_port

        self._server = await asyncio.start_server(
            self._handle_client,
            "127.0.0.1",
            self.listen_port,
        )
        sockets = self._server.sockets
        if sockets:
            self.listen_port = int(sockets[0].getsockname()[1])
        return self.listen_port

    async def drop(self) -> None:
        """Cierra el listener y corta abruptamente todas las conexiones TCP activas."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()

        for task in list(self._pipe_tasks):
            task.cancel()
        self._pipe_tasks.clear()

        for writer in list(self._active_writers):
            with suppress(Exception):
                writer.transport.abort()
        self._active_writers.clear()

        if server is not None:
            with suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=1.0)

    async def close(self) -> None:
        """Detiene el proxy liberando puertos y recursos."""
        await self.drop()


# ---------------------------------------------------------------------------
# Orquestador Oficial de Caos Cero-Nube
# ---------------------------------------------------------------------------
class ChaosRunner:
    """Inyecta fallos controlados sobre PostgreSQL/Redis locales y garantiza su restauración."""

    def __init__(
        self,
        postgres_container: str | None = None,
        redis_container: str | None = None,
        pg_host: str = "127.0.0.1",
        pg_port: int = 5432,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        recovery_timeout_seconds: float = 25.0,
        docker_bin: str = "docker",
        mode: Literal["auto", "docker", "tcp_proxy"] = "auto",
    ) -> None:
        self.postgres_container = postgres_container or os.getenv(
            "KC_CHAOS_PG_CONTAINER",
            DEFAULT_PG_CONTAINER,
        )
        self.redis_container = redis_container or os.getenv(
            "KC_CHAOS_REDIS_CONTAINER",
            DEFAULT_REDIS_CONTAINER,
        )
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.docker_bin = docker_bin
        self.mode = mode
        self._pg_mode: Literal["docker", "tcp_proxy"] | None = (
            mode if mode in {"docker", "tcp_proxy"} else None
        )
        self._redis_mode: Literal["docker", "tcp_proxy"] | None = (
            mode if mode in {"docker", "tcp_proxy"} else None
        )
        self._pg_proxy: _TcpFaultProxy | None = None
        self._redis_proxy: _TcpFaultProxy | None = None

    @property
    def active_pg_port(self) -> int:
        """Puerto TCP activo para conexiones de prueba hacia PostgreSQL."""
        if self._pg_proxy is not None and self._pg_proxy.listen_port > 0:
            return self._pg_proxy.listen_port
        return self.pg_port

    @property
    def active_redis_port(self) -> int:
        """Puerto TCP activo para conexiones de prueba hacia Redis."""
        if self._redis_proxy is not None and self._redis_proxy.listen_port > 0:
            return self._redis_proxy.listen_port
        return self.redis_port

    async def _run_docker(self, *args: str) -> tuple[int, str, str]:
        """Ejecuta un subcomando Docker sin shell manteniendo aislamiento seguro."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.docker_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return (127, "", str(exc))

        stdout_bytes, stderr_bytes = await proc.communicate()
        returncode = proc.returncode if proc.returncode is not None else 1
        return (
            returncode,
            stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr_bytes.decode("utf-8", errors="replace").strip(),
        )

    async def _container_exists(self, container_name: str) -> bool:
        rc, _, _ = await self._run_docker("inspect", container_name)
        return rc == 0

    async def _tcp_connectable(self, host: str, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=1.5,
            )
            writer.close()
            await writer.wait_closed()
        except OSError:
            return False
        return True

    async def _resolve_pg_mode(self) -> Literal["docker", "tcp_proxy"]:
        if self._pg_mode is not None:
            return self._pg_mode
        if await self._container_exists(self.postgres_container):
            self._pg_mode = "docker"
            return "docker"
        if await self._tcp_connectable(self.pg_host, self.pg_port):
            self._pg_mode = "tcp_proxy"
            return "tcp_proxy"
        self._pg_mode = "docker"
        return "docker"

    async def _resolve_redis_mode(self) -> Literal["docker", "tcp_proxy"]:
        if self._redis_mode is not None:
            return self._redis_mode
        if await self._container_exists(self.redis_container):
            self._redis_mode = "docker"
            return "docker"
        if await self._tcp_connectable(self.redis_host, self.redis_port):
            self._redis_mode = "tcp_proxy"
            return "tcp_proxy"
        self._redis_mode = "docker"
        return "docker"

    async def _check_postgres_ready(self) -> bool:
        """Comprueba que PostgreSQL responde tanto TCP como pg_isready."""
        mode = await self._resolve_pg_mode()
        if mode == "docker":
            rc, _, _ = await self._run_docker(
                "exec",
                self.postgres_container,
                "pg_isready",
                "-U",
                "postgres",
            )
            if rc != 0:
                return False
            return await self._tcp_connectable(self.pg_host, self.pg_port)

        return await self._tcp_connectable(self.pg_host, self.active_pg_port)

    async def _check_redis_ready(self) -> bool:
        """Comprueba que Redis responde +PONG al comando PING sobre TCP local."""
        target_port = self.active_redis_port
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.redis_host, target_port),
                timeout=1.5,
            )
            writer.write(b"*1\r\n$4\r\nPING\r\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=1.5)
            writer.close()
            await writer.wait_closed()
        except OSError:
            return False
        else:
            return b"PONG" in line

    async def ensure_postgres_up(self) -> None:
        """Asegura el arranque y disponibilidad completa de PostgreSQL local."""
        mode = await self._resolve_pg_mode()
        if mode == "tcp_proxy":
            if self._pg_proxy is None:
                self._pg_proxy = _TcpFaultProxy(self.pg_host, self.pg_port)
            await self._pg_proxy.start()
        else:
            rc, _, stderr = await self._run_docker("start", self.postgres_container)
            if rc != 0:
                msg = (
                    f"Fallo al iniciar contenedor PostgreSQL '{self.postgres_container}': {stderr}"
                )
                raise ChaosRecoveryError(msg)

        deadline = time.monotonic() + self.recovery_timeout_seconds
        while time.monotonic() < deadline:
            if await self._check_postgres_ready():
                return
            await asyncio.sleep(0.25)

        msg = (
            f"PostgreSQL '{self.postgres_container}' no respondió ready en "
            f"{self.recovery_timeout_seconds}s tras restauración."
        )
        raise ChaosRecoveryError(msg)

    async def ensure_redis_up(self) -> None:
        """Asegura el arranque y respuesta PING/PONG de Redis local."""
        mode = await self._resolve_redis_mode()
        if mode == "tcp_proxy":
            if self._redis_proxy is None:
                self._redis_proxy = _TcpFaultProxy(self.redis_host, self.redis_port)
            await self._redis_proxy.start()
        else:
            rc, _, stderr = await self._run_docker("start", self.redis_container)
            if rc != 0:
                msg = f"Fallo al iniciar contenedor Redis '{self.redis_container}': {stderr}"
                raise ChaosRecoveryError(msg)

        deadline = time.monotonic() + self.recovery_timeout_seconds
        while time.monotonic() < deadline:
            if await self._check_redis_ready():
                return
            await asyncio.sleep(0.25)

        msg = (
            f"Redis '{self.redis_container}' no respondió PONG en "
            f"{self.recovery_timeout_seconds}s tras restauración."
        )
        raise ChaosRecoveryError(msg)

    async def shutdown_proxies(self) -> None:
        """Libera los proxies TCP locales al finalizar el fixture de prueba."""
        if self._pg_proxy is not None:
            await self._pg_proxy.close()
            self._pg_proxy = None
        if self._redis_proxy is not None:
            await self._redis_proxy.close()
            self._redis_proxy = None

    async def simulate_db_drop(
        self,
        duration_seconds: float = 0.5,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Interrumpe PostgreSQL (Docker stop o corte TCP), cede control y restaura en finally."""
        mode = await self._resolve_pg_mode()
        logger.warning(
            "chaos.db_drop_start",
            container=self.postgres_container,
            mode=mode,
            duration_seconds=duration_seconds,
        )

        if mode == "tcp_proxy":
            if self._pg_proxy is None:
                self._pg_proxy = _TcpFaultProxy(self.pg_host, self.pg_port)
                await self._pg_proxy.start()
            await self._pg_proxy.drop()
            failure_type = "tcp_disconnect"
        else:
            rc, _, stderr = await self._run_docker("stop", "-t", "5", self.postgres_container)
            if rc != 0:
                msg = f"No se pudo detener contenedor PostgreSQL '{self.postgres_container}': {stderr}"
                raise ChaosRecoveryError(msg)
            failure_type = "docker_stop"

        try:
            if duration_seconds > 0:
                await asyncio.sleep(duration_seconds)
            yield {
                "target_service": "postgres",
                "container": self.postgres_container,
                "failure_type": failure_type,
                "status": "stopped",
                "duration_seconds": duration_seconds,
            }
        finally:
            await self.ensure_postgres_up()
            logger.info("chaos.db_restored", container=self.postgres_container, mode=mode)

    async def simulate_redis_drop(
        self,
        duration_seconds: float = 0.5,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Interrumpe Redis (Docker stop o corte TCP), cede control y restaura en finally."""
        mode = await self._resolve_redis_mode()
        logger.warning(
            "chaos.redis_drop_start",
            container=self.redis_container,
            mode=mode,
            duration_seconds=duration_seconds,
        )

        if mode == "tcp_proxy":
            if self._redis_proxy is None:
                self._redis_proxy = _TcpFaultProxy(self.redis_host, self.redis_port)
                await self._redis_proxy.start()
            await self._redis_proxy.drop()
            failure_type = "tcp_disconnect"
        else:
            rc, _, stderr = await self._run_docker("stop", "-t", "5", self.redis_container)
            if rc != 0:
                msg = f"No se pudo detener contenedor Redis '{self.redis_container}': {stderr}"
                raise ChaosRecoveryError(msg)
            failure_type = "docker_stop"

        try:
            if duration_seconds > 0:
                await asyncio.sleep(duration_seconds)
            yield {
                "target_service": "redis",
                "container": self.redis_container,
                "failure_type": failure_type,
                "status": "stopped",
                "duration_seconds": duration_seconds,
            }
        finally:
            await self.ensure_redis_up()
            logger.info("chaos.redis_restored", container=self.redis_container, mode=mode)

    @staticmethod
    def verify_circuit_breaker(endpoint_url: str) -> bool:
        """Verifica que el endpoint degrade controladamente (503 o ready=False) sin HTTP 500."""
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(endpoint_url)
        except httpx.RequestError as exc:
            logger.warning("chaos.circuit_probe_unreachable", url=endpoint_url, error=str(exc))
            return False

        if response.status_code == 503:
            logger.info("chaos.circuit_breaker_open_503", url=endpoint_url)
            return True

        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                return False

            if isinstance(body, dict):
                pg_down = body.get("postgres") is False
                redis_down = body.get("redis") is False
                degraded = body.get("estado") in {"degradado", "degraded", "error"}
                if pg_down or redis_down or degraded:
                    logger.info("chaos.circuit_breaker_ready_degraded", url=endpoint_url, body=body)
                    return True
            return False

        logger.error(
            "chaos.circuit_breaker_unexpected_status",
            url=endpoint_url,
            status_code=response.status_code,
        )
        return False

    @staticmethod
    def build_report(incidents: list[ChaosIncidentDTO]) -> ChaosTestReportDTO:
        """Consolida incidentes en un DTO inmutable prohibiendo falsos verdes vacíos."""
        if not incidents:
            return ChaosTestReportDTO(
                incidents=[],
                all_recovered=False,
                zero_gate_leaks=False,
            )

        all_recovered = all(item.recovered and item.state_consistent for item in incidents)
        zero_gate_leaks = all(item.gate_unapproved for item in incidents)
        return ChaosTestReportDTO(
            incidents=incidents,
            all_recovered=all_recovered,
            zero_gate_leaks=zero_gate_leaks,
        )

    @staticmethod
    def export_report_json(report: ChaosTestReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte de caos a JSON determinista listo para sellado WORM."""
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        payload = report.model_dump(mode="json")
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "chaos.report_exported",
            report_id=report.report_id,
            path=str(target_path),
        )
        return target_path
